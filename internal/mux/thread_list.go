package mux

import (
	"context"
	"encoding/json"
	"sync"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

func (m *Multiplexer) aggregateThreadList(request protocol.Message) {
	entries := m.childEntries()
	// Indexed by account order so merging is deterministic regardless of
	// which app-server answers first.
	results := make([]accountThreads, len(entries))
	var wait sync.WaitGroup
	for index, entry := range entries {
		wait.Add(1)
		go func(index int, entry childEntry) {
			defer wait.Done()
			results[index] = accountThreads{accountID: entry.account.ID, threads: m.listAllThreads(entry, request.Params)}
		}(index, entry)
	}
	wait.Wait()

	threads := mergeThreadLists(results, m.store.ThreadOwner, func(threadID, accountID string) {
		_ = m.store.SetThreadOwner(threadID, accountID)
	})
	sortThreads(threads)
	encoded, err := json.Marshal(map[string]any{"data": threads, "nextCursor": nil})
	if err != nil {
		m.write(protocol.Failure(request.ID, -32603, "failed to merge thread list"))
		return
	}
	m.write(protocol.Success(request.ID, encoded))
}

type accountThreads struct {
	accountID string
	threads   []map[string]any
}

// mergeThreadLists combines every account's history into one list with each
// thread appearing once.
//
// A chat that failed over (or was moved) is resumed from its rollout by the
// new account, so more than one app-server can list it. Ownership must not
// follow whichever account happened to answer last: the persisted owner is
// always kept, and only unknown threads are adopted.
func mergeThreadLists(
	results []accountThreads,
	currentOwner func(threadID string) (string, bool),
	adopt func(threadID, accountID string),
) []map[string]any {
	type seenThread struct {
		index     int
		accountID string
	}
	listedBy := make(map[string][]string)
	for _, result := range results {
		for _, thread := range result.threads {
			if threadID, ok := thread["id"].(string); ok && threadID != "" {
				listedBy[threadID] = append(listedBy[threadID], result.accountID)
			}
		}
	}
	threads := make([]map[string]any, 0)
	seen := make(map[string]seenThread)
	for _, result := range results {
		for _, thread := range result.threads {
			threadID, ok := thread["id"].(string)
			if !ok || threadID == "" {
				threads = append(threads, thread)
				continue
			}
			owner, known := currentOwner(threadID)
			ownerLists := known && containsString(listedBy[threadID], owner)
			previous, duplicate := seen[threadID]
			if duplicate {
				// Keep the owner's copy, which carries the latest turns.
				if ownerLists && result.accountID == owner && previous.accountID != owner {
					threads[previous.index] = thread
					seen[threadID] = seenThread{index: previous.index, accountID: result.accountID}
				}
				continue
			}
			seen[threadID] = seenThread{index: len(threads), accountID: result.accountID}
			threads = append(threads, thread)
			if !known {
				// Unknown thread: the first account in stable order that
				// lists it becomes the owner. A known owner is never replaced
				// here, because an owner missing from this listing usually
				// means its app-server failed to answer, not that it lost
				// the chat.
				adopt(threadID, result.accountID)
			}
		}
	}
	return threads
}

func containsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func (m *Multiplexer) listAllThreads(entry childEntry, originalParams json.RawMessage) []map[string]any {
	var params map[string]any
	if json.Unmarshal(originalParams, &params) != nil {
		params = make(map[string]any)
	}
	params["limit"] = 500
	threads := make([]map[string]any, 0)
	seenCursors := make(map[string]struct{})
	var cursor string
	for {
		if cursor == "" {
			params["cursor"] = nil
		} else {
			params["cursor"] = cursor
		}
		encodedParams, _ := json.Marshal(params)
		ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
		response, err := entry.child.Request(ctx, "thread/list", encodedParams)
		cancel()
		if err != nil {
			return threads
		}
		var decoded struct {
			Data       []map[string]any `json:"data"`
			NextCursor *string          `json:"nextCursor"`
		}
		if json.Unmarshal(response.Result, &decoded) != nil {
			return threads
		}
		threads = append(threads, decoded.Data...)
		if decoded.NextCursor == nil || *decoded.NextCursor == "" {
			return threads
		}
		cursor = *decoded.NextCursor
		if _, repeated := seenCursors[cursor]; repeated {
			return threads
		}
		seenCursors[cursor] = struct{}{}
	}
}
