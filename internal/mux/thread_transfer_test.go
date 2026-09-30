package mux

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

func TestTransferredThreadCompatibilityAndRefresh(t *testing.T) {
	for _, mode := range []string{"legacy", "missing", "previously-loaded", "stale-path", "running-stale-path", "unrelated-error"} {
		t.Run(mode, func(t *testing.T) {
			root := t.TempDir()
			source := filepath.Join(root, "source", "rollout-2026-test-thread.jsonl")
			home := filepath.Join(root, "target")
			destination := filepath.Join(home, "sessions", filepath.Base(source))
			if err := os.MkdirAll(filepath.Dir(source), 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(source, []byte("complete latest history\n"), 0600); err != nil {
				t.Fatal(err)
			}
			if mode == "previously-loaded" {
				os.MkdirAll(filepath.Dir(destination), 0700)
				os.WriteFile(destination, []byte("stale history"), 0600)
			}
			var methods []string
			request := func(_ context.Context, method string, _ json.RawMessage) (protocol.Message, error) {
				methods = append(methods, method)
				if len(methods) == 1 {
					switch mode {
					case "legacy":
						return protocol.Message{Result: json.RawMessage(fmt.Sprintf(`{"thread":{"path":%q}}`, source))}, nil
					case "missing":
						return protocol.Message{}, fmt.Errorf("thread/resume: no rollout found for thread id thread")
					case "running-stale-path":
						return protocol.Message{}, fmt.Errorf("cannot resume running thread thread with stale path: old path")
					case "stale-path":
						return protocol.Message{}, fmt.Errorf("cannot resume paginated thread thread with stale path: old path")
					case "previously-loaded":
						return protocol.Message{Result: json.RawMessage(fmt.Sprintf(`{"thread":{"path":%q}}`, destination))}, nil
					default:
						return protocol.Message{}, fmt.Errorf("authentication expired")
					}
				}
				if method == "thread/list" {
					return protocol.Message{Result: json.RawMessage(`{"data":[],"nextCursor":null}`)}, nil
				}
				if method == "thread/read" {
					return protocol.Message{Result: json.RawMessage(fmt.Sprintf(`{"thread":{"path":%q}}`, destination))}, nil
				}
				if method == "thread/resume" {
					data, err := os.ReadFile(destination)
					if err != nil || string(data) != "complete latest history\n" {
						t.Fatalf("resume must use latest copied history: %q %v", data, err)
					}
				}
				return protocol.Message{}, nil
			}
			err := resumeTransferredThread(context.Background(), "thread", source, home, json.RawMessage(`{}`), request)
			if mode == "unrelated-error" {
				if err == nil {
					t.Fatal("unrelated errors must be propagated")
				}
			} else if err != nil {
				t.Fatal(err)
			}
			expected := []string{"thread/resume"}
			if mode == "stale-path" || mode == "running-stale-path" {
				expected = append(expected, "thread/read", "thread/list", "thread/list", "thread/archive", "thread/unarchive", "thread/read", "thread/resume")
			}
			if mode == "missing" {
				expected = append(expected, "thread/resume")
			}
			if mode == "previously-loaded" {
				expected = append(expected, "thread/list", "thread/list", "thread/archive", "thread/unarchive", "thread/read", "thread/resume")
			}
			if !reflect.DeepEqual(methods, expected) {
				t.Fatalf("methods %v, expected %v", methods, expected)
			}
			if mode == "missing" || mode == "previously-loaded" {
				info, err := os.Stat(destination)
				if err != nil || info.Mode().Perm() != 0600 {
					t.Fatalf("private history permissions: %v %v", info, err)
				}
			}
		})
	}
}

func TestTransferredRolloutRejectsUnsafeDestination(t *testing.T) {
	home := t.TempDir()
	source := "/source/rollout-2026-test-thread.jsonl"
	for _, destination := range []string{filepath.Join(home, "auth.json"), filepath.Join(home, "..", "sessions", filepath.Base(source)), filepath.Join(home, "sessions", "other.jsonl")} {
		if err := validateTransferredRollout("thread", source, destination, home); err == nil {
			t.Fatalf("accepted %s", destination)
		}
	}
	if err := validateTransferredRollout("thread", source, filepath.Join(home, "sessions", "2026", "09", filepath.Base(source)), home); err != nil {
		t.Fatal(err)
	}
}

func TestTransferredRolloutRejectsSymlinkParent(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	source := filepath.Join(root, "rollout-2026-test-thread.jsonl")
	os.WriteFile(source, []byte("history"), 0600)
	linked := filepath.Join(root, "sessions")
	if err := os.Symlink(outside, linked); err != nil {
		t.Skip(err)
	}
	destination := filepath.Join(linked, filepath.Base(source))
	if err := copyTransferredRollout(source, destination, root); err == nil {
		t.Fatal("symlink parent accepted")
	}
	if _, err := os.Stat(filepath.Join(outside, filepath.Base(source))); !os.IsNotExist(err) {
		t.Fatal("outside file was created")
	}
}

func TestSpawnedVisibilityAndActiveTaskProtection(t *testing.T) {
	for _, active := range []bool{false, true} {
		t.Run(fmt.Sprint(active), func(t *testing.T) {
			requester := func(_ context.Context, _ string, params json.RawMessage) (protocol.Message, error) {
				var p struct {
					Archived bool `json:"archived"`
				}
				json.Unmarshal(params, &p)
				data := `[{"id":"child","parentThreadId":"root","status":{"type":"idle"}},{"id":"unrelated","parentThreadId":"other"}]`
				if p.Archived {
					data = `[{"id":"hidden","parentThreadId":"child"}]`
				} else if active {
					data = `[{"id":"child","parentThreadId":"root","status":{"type":"active"}}]`
				}
				return protocol.Message{Result: json.RawMessage(`{"data":` + data + `,"nextCursor":null}`)}, nil
			}
			ids, err := visibleSpawnSubtree(context.Background(), "root", requester)
			if active {
				if err == nil {
					t.Fatal("active descendant permitted")
				}
				return
			}
			if err != nil || !reflect.DeepEqual(ids, []string{"root", "child"}) {
				t.Fatalf("restore %v err %v", ids, err)
			}
		})
	}
}
func TestTransferLifecycleNotificationsAreScopedAndConsumedOnce(t *testing.T) {
	m := &Multiplexer{}
	cancel := m.hideTransferLifecycle("account", []string{"root", "child"})
	event := protocol.Message{Method: "thread/archived", Params: json.RawMessage(`{"threadId":"child"}`)}
	if m.consumeTransferLifecycle("other", event) {
		t.Fatal("another account hidden")
	}
	if !m.consumeTransferLifecycle("account", event) {
		t.Fatal("internal archive visible")
	}
	if m.consumeTransferLifecycle("account", event) {
		t.Fatal("later user archive hidden")
	}
	cancel()
	if m.consumeTransferLifecycle("account", protocol.Message{Method: "thread/unarchived", Params: json.RawMessage(`{"threadId":"root"}`)}) {
		t.Fatal("failed transfer marker remained")
	}
}
