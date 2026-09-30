package mux

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

type threadRequester func(context.Context, string, json.RawMessage) (protocol.Message, error)

// Older engines accept an explicit rollout path. New engines resolve the ID
// against their account-local index, so materialize history there before retrying.
// A previously loaded target must be unloaded before replacing its older copy.
func resumeTransferredThread(ctx context.Context, threadID, sourcePath, targetHome string, params json.RawMessage, request threadRequester, hideLifecycle ...func([]string) func()) (transferErr error) {
	response, err := request(ctx, "thread/resume", params)
	var resumed struct {
		Thread struct {
			Path string `json:"path"`
		} `json:"thread"`
	}
	if err == nil {
		if decodeErr := json.Unmarshal(response.Result, &resumed); decodeErr != nil {
			return fmt.Errorf("decode resumed chat: %w", decodeErr)
		}
		if resumed.Thread.Path == sourcePath {
			return nil
		}
	} else if strings.Contains(err.Error(), "cannot resume paginated thread "+threadID+" with stale path:") || strings.Contains(err.Error(), "cannot resume running thread "+threadID+" with stale path:") {
		readParams, _ := json.Marshal(map[string]any{"threadId": threadID, "includeTurns": false})
		current, readErr := request(ctx, "thread/read", readParams)
		if readErr != nil {
			return readErr
		}
		if decodeErr := json.Unmarshal(current.Result, &resumed); decodeErr != nil {
			return decodeErr
		}
	} else if !strings.Contains(err.Error(), "no rollout found for thread id "+threadID) {
		return err
	}
	destination := resumed.Thread.Path
	if destination == "" {
		destination = filepath.Join(targetHome, "sessions", filepath.Base(sourcePath))
	}
	if err := validateTransferredRollout(threadID, sourcePath, destination, targetHome); err != nil {
		return err
	}
	if resumed.Thread.Path != "" {
		// Unsubscribe only detaches notifications; it leaves the engine and
		// its writer alive. Archive waits for shutdown; unarchive preserves
		// the chat's visible state before replacing the obsolete snapshot.
		restoreIDs, err := visibleSpawnSubtree(ctx, threadID, request)
		if err != nil {
			return err
		}
		if len(hideLifecycle) > 0 {
			cancel := hideLifecycle[0](restoreIDs)
			defer func() {
				if transferErr != nil {
					cancel()
				}
			}()
		}
		threadParams, _ := json.Marshal(map[string]any{"threadId": threadID})
		if _, err := request(ctx, "thread/archive", threadParams); err != nil {
			return fmt.Errorf("stop previous chat writer: %w", err)
		}
		var restoreErr error
		for _, id := range restoreIDs {
			restoreParams, _ := json.Marshal(map[string]any{"threadId": id})
			if _, err := request(ctx, "thread/unarchive", restoreParams); err != nil && restoreErr == nil {
				restoreErr = fmt.Errorf("restore chat visibility: %w", err)
			}
		}
		if restoreErr != nil {
			return restoreErr
		}
		readParams, _ := json.Marshal(map[string]any{"threadId": threadID, "includeTurns": false})
		current, err := request(ctx, "thread/read", readParams)
		if err != nil {
			return err
		}
		if err := json.Unmarshal(current.Result, &resumed); err != nil {
			return err
		}
		destination = resumed.Thread.Path
		if err := validateTransferredRollout(threadID, sourcePath, destination, targetHome); err != nil {
			return err
		}
	}
	if err := copyTransferredRollout(sourcePath, destination, targetHome); err != nil {
		return fmt.Errorf("copy chat history: %w", err)
	}
	var localParams map[string]any
	if err := json.Unmarshal(params, &localParams); err != nil {
		return err
	}
	localParams["path"] = destination
	localResume, err := json.Marshal(localParams)
	if err != nil {
		return err
	}
	_, err = request(ctx, "thread/resume", localResume)
	return err
}

func validateTransferredRollout(threadID, sourcePath, destination, targetHome string) error {
	if threadID == "" || strings.ContainsAny(threadID, `/\\`) || !strings.HasPrefix(filepath.Base(sourcePath), "rollout-") || !strings.HasSuffix(filepath.Base(sourcePath), "-"+threadID+".jsonl") {
		return fmt.Errorf("existing chat has an invalid rollout path")
	}
	relative, err := filepath.Rel(targetHome, destination)
	if err != nil || filepath.IsAbs(relative) {
		return fmt.Errorf("target chat history is outside its account home")
	}
	parts := strings.Split(relative, string(filepath.Separator))
	if len(parts) < 2 || (parts[0] != "sessions" && parts[0] != "archived_sessions") || strings.Contains(relative, ".."+string(filepath.Separator)) || filepath.Base(destination) != filepath.Base(sourcePath) {
		return fmt.Errorf("target chat history is outside its session directory")
	}
	return nil
}

func copyTransferredRollout(sourcePath, destination, targetHome string) error {
	source, err := os.Open(sourcePath)
	if err != nil {
		return err
	}
	defer source.Close()
	info, err := source.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("chat history is not a regular file")
	}
	if err := ensureRolloutDirectory(filepath.Dir(destination), targetHome); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(destination), ".codex-mux-rollout-*")
	if err != nil {
		return err
	}
	defer os.Remove(temporary.Name())
	defer temporary.Close()
	if _, err := io.Copy(temporary, source); err != nil {
		return err
	}
	if err := temporary.Sync(); err != nil {
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporary.Name(), destination)
}

// Reject symlinked directories before creating any missing component.
func ensureRolloutDirectory(directory, targetHome string) error {
	clean := filepath.Clean(directory)
	for parent := clean; ; parent = filepath.Dir(parent) {
		if parent == filepath.Clean(targetHome) {
			break
		}
		info, err := os.Lstat(parent)
		if err == nil {
			if info.Mode()&os.ModeSymlink != 0 {
				return fmt.Errorf("chat history directory is a symlink: %s", parent)
			}
			if !info.IsDir() {
				return fmt.Errorf("chat history parent is not a directory")
			}
		} else if !os.IsNotExist(err) {
			return err
		}
		if parent == filepath.Clean(targetHome) || parent == filepath.Dir(parent) {
			break
		}
	}
	return os.MkdirAll(clean, 0o700)
}

// Archive stops writers recursively. Preserve descendant visibility and avoid
// stopping an active spawned task while refreshing an obsolete account copy.
func visibleSpawnSubtree(ctx context.Context, root string, request threadRequester) ([]string, error) {
	type listedThread struct {
		ID     string          `json:"id"`
		Parent string          `json:"parentThreadId"`
		Source json.RawMessage `json:"source"`
		Status struct {
			Type string `json:"type"`
		} `json:"status"`
	}
	type entry struct {
		thread   listedThread
		archived bool
	}
	var entries []entry
	for _, archived := range []bool{false, true} {
		cursor := ""
		seen := map[string]bool{}
		for {
			options := map[string]any{"archived": archived, "limit": 500, "sourceKinds": []string{"subAgent", "subAgentThreadSpawn"}}
			if cursor != "" {
				options["cursor"] = cursor
			}
			params, _ := json.Marshal(options)
			response, err := request(ctx, "thread/list", params)
			if err != nil {
				return nil, fmt.Errorf("read spawned chats before transfer: %w", err)
			}
			var page struct {
				Data   []listedThread `json:"data"`
				Cursor *string        `json:"nextCursor"`
			}
			if err := json.Unmarshal(response.Result, &page); err != nil {
				return nil, err
			}
			for _, thread := range page.Data {
				entries = append(entries, entry{thread, archived})
			}
			if page.Cursor == nil || *page.Cursor == "" {
				break
			}
			if seen[*page.Cursor] {
				return nil, fmt.Errorf("spawned chat listing repeated its cursor")
			}
			cursor = *page.Cursor
			seen[cursor] = true
		}
	}
	descendants := map[string]bool{root: true}
	for changed := true; changed; {
		changed = false
		for _, entry := range entries {
			parent := entry.thread.Parent
			if parent == "" {
				var source struct {
					SubAgent struct {
						Spawn struct {
							Parent string `json:"parent_thread_id"`
						} `json:"thread_spawn"`
					} `json:"subAgent"`
				}
				if json.Unmarshal(entry.thread.Source, &source) == nil {
					parent = source.SubAgent.Spawn.Parent
				}
			}
			if descendants[parent] && !descendants[entry.thread.ID] {
				descendants[entry.thread.ID] = true
				changed = true
			}
		}
	}
	restore := []string{root}
	for _, entry := range entries {
		if entry.thread.ID != root && descendants[entry.thread.ID] {
			if entry.thread.Status.Type == "active" {
				return nil, fmt.Errorf("wait for spawned tasks to finish before switching subscriptions")
			}
			if !entry.archived {
				restore = append(restore, entry.thread.ID)
			}
		}
	}
	return restore, nil
}
