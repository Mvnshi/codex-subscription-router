package mux

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

const (
	lineageSession = "01a14fd5-e6f0-7a92-be3d-3dee17194b48"
	lineageMiddle  = "01a195fd-5294-7cf3-8778-36412028ba88"
	lineageChild   = "01a1d12b-83f0-7c43-bef3-e15b0c25c848"
)

type lineageFixture struct {
	sourceHome string
	targetHome string
	rootFile   string
	middleFile string
	childFile  string
}

// newLineageFixture lays out the shape the current engine produces: a
// compressed root, a chat built on it, and a forked chat built on that one.
func newLineageFixture(t *testing.T) lineageFixture {
	t.Helper()
	root := t.TempDir()
	f := lineageFixture{sourceHome: filepath.Join(root, "primary"), targetHome: filepath.Join(root, "account")}
	f.rootFile = filepath.Join(f.sourceHome, "sessions", "2026", "10", "18", "rollout-2026-10-18T12-26-21-"+lineageSession+".jsonl.zst")
	f.middleFile = filepath.Join(f.sourceHome, "sessions", "2026", "11", "01", "rollout-2026-11-01T02-22-49-"+lineageSession+"_"+lineageMiddle+".jsonl")
	f.childFile = filepath.Join(f.sourceHome, "sessions", "2026", "11", "12", "rollout-2026-11-12T14-10-52-"+lineageSession+"_"+lineageChild+".jsonl")
	for path, content := range map[string]string{
		f.rootFile:   "compressed root bytes",
		f.middleFile: sessionMeta(lineageSession, lineageSession) + "\nmiddle history\n",
		f.childFile:  sessionMeta(lineageSession, lineageMiddle) + "\nnewest history\n",
	} {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return f
}

// sessionMeta is the first line of a paginated chat. As the real engine writes
// it, the header id is the chat's root, whatever the chat's own id is.
func sessionMeta(headerID, baseThread string) string {
	return fmt.Sprintf(`{"type":"session_meta","payload":{"session_id":%q,"id":%q,"history_mode":"paginated","history_base":{"thread_id":%q,"end_ordinal_exclusive":7}}}`, headerID, headerID, baseThread)
}

func TestRolloutHistoryBaseReadsOnlyPaginatedHistory(t *testing.T) {
	f := newLineageFixture(t)
	if got := rolloutHistoryBase(f.childFile); got != lineageMiddle {
		t.Fatalf("child base %q", got)
	}
	if got := rolloutHistoryBase(f.rootFile); got != "" {
		t.Fatalf("a compressed root has no readable base, got %q", got)
	}
	plain := filepath.Join(t.TempDir(), "rollout-2026-test-thread.jsonl")
	for _, content := range []string{"complete latest history\n", `{"type":"session_meta","payload":{"id":"x"}}` + "\n", `{"type":"event_msg"}` + "\n", ""} {
		if err := os.WriteFile(plain, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
		if got := rolloutHistoryBase(plain); got != "" {
			t.Fatalf("%q must not look paginated, got %q", content, got)
		}
	}
	if got := rolloutHistoryBase(filepath.Join(t.TempDir(), "missing.jsonl")); got != "" {
		t.Fatalf("missing file: %q", got)
	}
}

func TestForkStyleRolloutNamesAreValid(t *testing.T) {
	home := t.TempDir()
	source := filepath.Join("/source", "rollout-2026-11-12T14-10-52-"+lineageSession+"_"+lineageChild+".jsonl")
	destination := filepath.Join(home, "sessions", "2026", "11", "12", filepath.Base(source))
	if err := validateTransferredRollout(lineageChild, source, destination, home); err != nil {
		t.Fatalf("fork-style name rejected: %v", err)
	}
	if err := validateTransferredRollout("someone-else", source, destination, home); err == nil {
		t.Fatal("a rollout for a different chat must still be rejected")
	}
}

func TestPaginatedChatBringsEveryOlderPartToTheTarget(t *testing.T) {
	f := newLineageFixture(t)
	var calls []string
	var resumedPath string
	request := func(_ context.Context, method string, params json.RawMessage) (protocol.Message, error) {
		calls = append(calls, method)
		var body map[string]any
		_ = json.Unmarshal(params, &body)
		resumedPath, _ = body["path"].(string)
		return protocol.Message{}, nil
	}
	params := json.RawMessage(fmt.Sprintf(`{"threadId":%q,"path":%q,"modelProvider":"subscription-fallback"}`, lineageChild, f.childFile))
	if err := resumeTransferredThread(context.Background(), lineageChild, f.childFile, f.targetHome, params, request); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(calls, []string{"thread/resume"}) {
		t.Fatalf("calls %v", calls)
	}
	want := filepath.Join(f.targetHome, "sessions", "2026", "11", "12", filepath.Base(f.childFile))
	if resumedPath != want {
		t.Fatalf("resumed from %q, want the local copy %q", resumedPath, want)
	}
	// Older parts arrive byte for byte; the chat's own file arrives with only its header "id" corrected to its own
	// id (its session_id stays the root's).
	correctedChild := strings.Replace(sessionMeta(lineageSession, lineageMiddle), `"id":"`+lineageSession+`"`, `"id":"`+lineageChild+`"`, 1)
	for source, content := range map[string]string{
		f.rootFile:   "compressed root bytes",
		f.middleFile: sessionMeta(lineageSession, lineageSession) + "\nmiddle history\n",
		f.childFile:  correctedChild + "\nnewest history\n",
	} {
		relative, _ := filepath.Rel(f.sourceHome, source)
		data, err := os.ReadFile(filepath.Join(f.targetHome, relative))
		if err != nil || string(data) != content {
			t.Fatalf("%s was not copied as expected: %q %v", relative, data, err)
		}
	}
	// The original is never edited.
	original, _ := os.ReadFile(f.childFile)
	if string(original) != sessionMeta(lineageSession, lineageMiddle)+"\nnewest history\n" {
		t.Fatalf("the source chat was modified: %q", original)
	}
}

func TestRewriteRolloutHeaderID(t *testing.T) {
	const root, fork = "01a14fd5-e6f0-7a92-be3d-3dee17194b48", "01a1d12b-83f0-7c43-bef3-e15b0c25c848"
	for name, test := range map[string]struct {
		line    string
		want    string
		changed bool
		failure string
	}{
		"compact, session_id decoy first": {
			line:    `{"payload":{"session_id":"` + root + `","id":"` + root + `","x":1}}`,
			want:    `{"payload":{"session_id":"` + root + `","id":"` + fork + `","x":1}}`,
			changed: true,
		},
		"spaces around the colon": {
			line:    `{"payload":{"id" : "` + root + `"}}`,
			want:    `{"payload":{"id" : "` + fork + `"}}`,
			changed: true,
		},
		"already the chat's own id": {
			line: `{"payload":{"id":"` + fork + `"}}`,
			want: `{"payload":{"id":"` + fork + `"}}`,
		},
		"no id at all": {
			line: `{"payload":{"x":1}}`,
			want: `{"payload":{"x":1}}`,
		},
		"the id appears twice": {
			line:    `{"payload":{"id":"` + root + `","git":{"id":"` + root + `"}}}`,
			failure: "found 2 places",
		},
		"not json": {
			line:    `not json`,
			failure: "read chat history header",
		},
	} {
		t.Run(name, func(t *testing.T) {
			got, changed, err := rewriteRolloutHeaderID([]byte(test.line), fork)
			if test.failure != "" {
				if err == nil || !strings.Contains(err.Error(), test.failure) {
					t.Fatalf("error %v, want one containing %q", err, test.failure)
				}
				return
			}
			if err != nil || changed != test.changed || string(got) != test.want {
				t.Fatalf("got %q changed=%v err=%v, want %q changed=%v", got, changed, err, test.want, test.changed)
			}
			if len(got) != len(test.line) {
				t.Fatalf("the correction must not move any later byte: %d -> %d", len(test.line), len(got))
			}
		})
	}
}

func TestPaginatedChatWithAMissingOlderPartFailsWithAClearMessage(t *testing.T) {
	f := newLineageFixture(t)
	if err := os.Remove(f.middleFile); err != nil {
		t.Fatal(err)
	}
	called := false
	request := func(context.Context, string, json.RawMessage) (protocol.Message, error) {
		called = true
		return protocol.Message{}, nil
	}
	err := resumeTransferredThread(context.Background(), lineageChild, f.childFile, f.targetHome, json.RawMessage(`{}`), request)
	if err == nil || !strings.Contains(err.Error(), "missing from the source account") || !strings.Contains(err.Error(), lineageMiddle) {
		t.Fatalf("error %v", err)
	}
	if called {
		t.Fatal("the target engine must not be asked to resume a chat whose history is incomplete")
	}
	if _, statErr := os.Stat(filepath.Join(f.targetHome, "sessions", "2026", "11", "12", filepath.Base(f.childFile))); statErr == nil {
		t.Fatal("a half-copied chat must not be left in the target")
	}
}

func TestCircularLineageIsRefused(t *testing.T) {
	f := newLineageFixture(t)
	// The middle chat now claims to be built on the child that is built on it.
	if err := os.WriteFile(f.middleFile, []byte(sessionMeta(lineageSession, lineageChild)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	err := copyRolloutLineage(f.childFile, f.targetHome)
	if err == nil || !strings.Contains(err.Error(), "refers back to itself") {
		t.Fatalf("error %v", err)
	}
}

func TestOlderPartsAreNotCopiedAgainWhenTheTargetHasThem(t *testing.T) {
	f := newLineageFixture(t)
	if err := copyRolloutLineage(f.childFile, f.targetHome); err != nil {
		t.Fatal(err)
	}
	relative, _ := filepath.Rel(f.sourceHome, f.rootFile)
	marker := filepath.Join(f.targetHome, relative)
	// Same size, different bytes: a copy that was skipped keeps the marker.
	if err := os.WriteFile(marker, []byte("COMPRESSED ROOT BYTES"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := copyRolloutLineage(f.childFile, f.targetHome); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(marker)
	if string(data) != "COMPRESSED ROOT BYTES" {
		t.Fatalf("an older part of the same size was copied again: %q", data)
	}
}

// A target that already holds a copy goes through the general refresh path. For
// a chat built on an older one the engine names the chat's root in its refusal,
// not the chat being resumed, and the refresh must still recognise it.
func TestPaginatedChatAlreadyOnTheTargetIsRefreshed(t *testing.T) {
	f := newLineageFixture(t)
	destination := filepath.Join(f.targetHome, "sessions", "2026", "11", "12", filepath.Base(f.childFile))
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(destination, []byte("stale copy"), 0o600); err != nil {
		t.Fatal(err)
	}
	var methods []string
	request := func(_ context.Context, method string, _ json.RawMessage) (protocol.Message, error) {
		methods = append(methods, method)
		switch {
		case len(methods) == 1:
			return protocol.Message{}, fmt.Errorf("cannot resume paginated thread %s with stale path: requested %s, current %s; omit path and resume by thread id", lineageSession, f.childFile, destination)
		case method == "thread/list":
			return protocol.Message{Result: json.RawMessage(`{"data":[],"nextCursor":null}`)}, nil
		case method == "thread/read":
			return protocol.Message{Result: json.RawMessage(fmt.Sprintf(`{"thread":{"path":%q}}`, destination))}, nil
		}
		return protocol.Message{}, nil
	}
	if err := resumeTransferredThread(context.Background(), lineageChild, f.childFile, f.targetHome, json.RawMessage(`{}`), request); err != nil {
		t.Fatal(err)
	}
	want := []string{"thread/resume", "thread/read", "thread/list", "thread/list", "thread/archive", "thread/unarchive", "thread/read", "thread/resume"}
	if !reflect.DeepEqual(methods, want) {
		t.Fatalf("methods %v, want %v", methods, want)
	}
	data, _ := os.ReadFile(destination)
	if !strings.Contains(string(data), "newest history") {
		t.Fatalf("the refreshed copy is not the latest history: %q", data)
	}
}
