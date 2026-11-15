package mux

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// Current engines write a chat in "paginated" form: the chat's own rollout file
// holds only its newest part, and its first line (session_meta) names the chat
// the older history lives in:
//
//	{"type":"session_meta","payload":{"history_mode":"paginated",
//	   "history_base":{"thread_id":"<older chat>", ...}, ...}}
//
// That older chat is itself a rollout file, which may point further back, down
// to a root that can even be zstd-compressed. An engine resuming the chat looks
// the whole chain up in its own account home, so moving the chat to another
// subscription has to bring every file in the chain along, not only the newest.
// Without them the target engine answers "invalid paginated history lineage ...
// missing source rollout".

const (
	// maxRolloutMetaLine bounds how much of a rollout's first line is read.
	maxRolloutMetaLine = 4 << 20
	// maxLineageDepth stops a corrupt chain that refers back to itself.
	maxLineageDepth = 64
)

type rolloutMeta struct {
	Type    string `json:"type"`
	Payload struct {
		HistoryBase *struct {
			ThreadID string `json:"thread_id"`
		} `json:"history_base"`
	} `json:"payload"`
}

// readRolloutFirstLine returns a rollout's first line including its line
// terminator, refusing a file whose first line is implausibly long.
func readRolloutFirstLine(reader *bufio.Reader) ([]byte, error) {
	var line []byte
	for {
		chunk, err := reader.ReadSlice('\n')
		line = append(line, chunk...)
		if len(line) > maxRolloutMetaLine {
			return nil, errors.New("chat history header is too long")
		}
		if err == bufio.ErrBufferFull {
			continue
		}
		if err != nil && len(line) == 0 {
			return nil, err
		}
		return line, nil
	}
}

// rolloutHistoryBase returns the id of the chat whose history the rollout at
// path continues, or "" when the file is a complete history or its first line
// is not a session_meta it understands (older engines, compressed files).
func rolloutHistoryBase(path string) string {
	if strings.HasSuffix(path, ".zst") {
		return ""
	}
	file, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer file.Close()
	line, err := readRolloutFirstLine(bufio.NewReaderSize(file, 64*1024))
	if err != nil {
		return ""
	}
	var meta rolloutMeta
	if json.Unmarshal(line, &meta) != nil || meta.Type != "session_meta" || meta.Payload.HistoryBase == nil {
		return ""
	}
	return strings.TrimSpace(meta.Payload.HistoryBase.ThreadID)
}

// rolloutAccountHome returns the account home a rollout lives in: the parent of
// its "sessions" or "archived_sessions" directory.
func rolloutAccountHome(rolloutPath string) (string, bool) {
	for dir := filepath.Dir(rolloutPath); ; {
		if name := filepath.Base(dir); name == "sessions" || name == "archived_sessions" {
			return filepath.Dir(dir), true
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", false
		}
		dir = parent
	}
}

// rolloutNameMatchesThread reports whether a rollout file name belongs to the
// chat. A plain chat is rollout-<time>-<id>.jsonl; a chat that continues
// another is rollout-<time>-<session>_<id>.jsonl; a root may be compressed.
func rolloutNameMatchesThread(name, threadID string) bool {
	if !strings.HasPrefix(name, "rollout-") {
		return false
	}
	for _, suffix := range []string{".jsonl", ".jsonl.zst"} {
		if strings.HasSuffix(name, "-"+threadID+suffix) || strings.HasSuffix(name, "_"+threadID+suffix) {
			return true
		}
	}
	return false
}

// findRolloutForThread looks a chat's rollout up in an account home. An
// uncompressed file wins over a compressed one.
func findRolloutForThread(home, threadID string) (string, bool) {
	if threadID == "" || strings.ContainsAny(threadID, `/\`) {
		return "", false
	}
	compressed := ""
	for _, directory := range []string{"sessions", "archived_sessions"} {
		found := ""
		_ = filepath.WalkDir(filepath.Join(home, directory), func(path string, entry fs.DirEntry, err error) error {
			if err != nil || entry.IsDir() || !rolloutNameMatchesThread(entry.Name(), threadID) {
				return nil
			}
			if strings.HasSuffix(entry.Name(), ".zst") {
				if compressed == "" {
					compressed = path
				}
				return nil
			}
			found = path
			return fs.SkipAll
		})
		if found != "" {
			return found, true
		}
	}
	return compressed, compressed != ""
}

// rolloutDestination maps a rollout in sourceHome to the same relative place
// under targetHome, refusing anything outside the session directories.
func rolloutDestination(sourceHome, file, targetHome string) (string, error) {
	relative, err := filepath.Rel(sourceHome, file)
	if err != nil || filepath.IsAbs(relative) {
		return "", errors.New("chat history is outside its account home")
	}
	parts := strings.Split(relative, string(filepath.Separator))
	if len(parts) < 2 || (parts[0] != "sessions" && parts[0] != "archived_sessions") || strings.Contains(relative, ".."+string(filepath.Separator)) {
		return "", errors.New("chat history is outside its session directory")
	}
	return filepath.Join(targetHome, relative), nil
}

// copyRolloutLineage copies into targetHome every rollout the chat at
// sourcePath is built on. A file that is already there with the same size is
// left alone: older parts do not change, and some are over a hundred megabytes.
func copyRolloutLineage(sourcePath, targetHome string) error {
	sourceHome, ok := rolloutAccountHome(sourcePath)
	if !ok {
		return errors.New("existing chat is not inside a sessions directory")
	}
	current := sourcePath
	seen := map[string]bool{}
	for depth := 0; depth < maxLineageDepth; depth++ {
		base := rolloutHistoryBase(current)
		if base == "" {
			return nil
		}
		if seen[base] {
			return errors.New("chat history refers back to itself")
		}
		seen[base] = true
		file, ok := findRolloutForThread(sourceHome, base)
		if !ok {
			return fmt.Errorf("chat history is built on chat %s, which is missing from the source account", base)
		}
		destination, err := rolloutDestination(sourceHome, file, targetHome)
		if err != nil {
			return err
		}
		if !sameSizeFile(file, destination) {
			if err := copyTransferredRollout(file, destination, targetHome); err != nil {
				return fmt.Errorf("copy older chat history %s: %w", filepath.Base(file), err)
			}
		}
		current = file
	}
	return fmt.Errorf("chat history is nested more than %d levels deep", maxLineageDepth)
}

// A chat that continues an older one keeps, in its own file's header, the id of
// the chat it started from (its root), not its own: its own id is only in the
// file name and in the index of the account that created it. An engine that
// learns the chat from the file alone therefore registers it under the root's
// id, collides with the root, and answers "thread not found" for the id the app
// keeps using. The copy sent to another account gets its header corrected; the
// original is never touched. The two ids have the same length, so no offset
// into the file moves.
func rewriteRolloutHeaderID(line []byte, threadID string) ([]byte, bool, error) {
	var header struct {
		Payload struct {
			ID string `json:"id"`
		} `json:"payload"`
	}
	if err := json.Unmarshal(line, &header); err != nil {
		return nil, false, fmt.Errorf("read chat history header: %w", err)
	}
	old := header.Payload.ID
	if old == "" || old == threadID {
		return line, false, nil
	}
	pattern := regexp.MustCompile(`("id"\s*:\s*")` + regexp.QuoteMeta(old) + `"`)
	if matches := pattern.FindAllIndex(line, -1); len(matches) != 1 {
		return nil, false, fmt.Errorf("cannot correct the chat id in its history header (found %d places)", len(matches))
	}
	location := pattern.FindSubmatchIndex(line)
	fixed := make([]byte, 0, len(line)-len(old)+len(threadID))
	fixed = append(fixed, line[:location[3]]...)
	fixed = append(fixed, threadID...)
	fixed = append(fixed, '"')
	fixed = append(fixed, line[location[1]:]...)
	return fixed, true, nil
}

// copyRolloutForThread copies the chat's own rollout to the target. A paginated
// chat gets the header correction above; anything else is copied as it is.
func copyRolloutForThread(sourcePath, destination, targetHome, threadID string) error {
	if rolloutHistoryBase(sourcePath) == "" {
		return copyTransferredRollout(sourcePath, destination, targetHome)
	}
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
		return errors.New("chat history is not a regular file")
	}
	reader := bufio.NewReaderSize(source, 64*1024)
	line, err := readRolloutFirstLine(reader)
	if err != nil {
		return err
	}
	fixed, changed, err := rewriteRolloutHeaderID(line, threadID)
	if err != nil {
		return err
	}
	if !changed {
		return copyTransferredRollout(sourcePath, destination, targetHome)
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
	if _, err := temporary.Write(fixed); err != nil {
		return err
	}
	if _, err := io.Copy(temporary, reader); err != nil {
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

func sameSizeFile(left, right string) bool {
	leftInfo, err := os.Stat(left)
	if err != nil {
		return false
	}
	rightInfo, err := os.Stat(right)
	return err == nil && leftInfo.Size() == rightInfo.Size()
}
