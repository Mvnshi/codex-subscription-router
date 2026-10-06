package mux

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
	"github.com/b-nnett/codex-subscription-router/internal/state"
)

// These tests drive the real Multiplexer the way the desktop app does (client
// messages in, protocol messages out) against two real child processes and the
// real filesystem. They exercise the behaviour the signed-app smoke test checks
// by hand: where new chats go, what happens when an account is spent, and that a
// chat moved to another account keeps its history and its turn. Because the
// children are real processes and the history move is a real file copy and
// rename, they also cover the parts that differ between operating systems.

// TestFailoverHelperProcess is a scripted engine shaped like the newer Codex
// engine. Per CODEX_HOME it reads: account.json and ratelimits.json (identity
// and usage), label (names the account in history), and two switches the test
// flips at run time: "exhausted" (a turn is accepted, then dies with a usage
// limit notification) and "exhausted-response" (turn/start itself is rejected
// with a usage limit error).
func TestFailoverHelperProcess(t *testing.T) {
	if os.Getenv("GO_WANT_FAILOVER_HELPER") != "1" {
		return
	}
	home := os.Getenv("CODEX_HOME")
	read := func(name string) (string, bool) {
		data, err := os.ReadFile(filepath.Join(home, name))
		return strings.TrimSpace(string(data)), err == nil
	}
	exists := func(name string) bool {
		_, err := os.Stat(filepath.Join(home, name))
		return err == nil
	}
	label, _ := read("label")
	threads := map[string]string{} // thread id -> rollout path this engine has loaded
	sequence := 0

	write := func(value any) {
		encoded, _ := json.Marshal(value)
		fmt.Fprintf(os.Stdout, "%s\n", encoded)
	}
	reply := func(id json.RawMessage, result any) {
		write(map[string]any{"id": id, "result": result})
	}
	fail := func(id json.RawMessage, code int, message string) {
		write(map[string]any{"id": id, "error": map[string]any{"code": code, "message": message}})
	}
	notify := func(method string, params any) {
		write(map[string]any{"method": method, "params": params})
	}
	threadResult := func(id, path string) map[string]any {
		return map[string]any{"thread": map[string]any{"id": id, "path": path, "cwd": home, "modelProvider": "openai"}}
	}
	insideHome := func(path string) bool {
		relative, err := filepath.Rel(home, path)
		return err == nil && !strings.HasPrefix(relative, "..")
	}

	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 64*1024), 16*1024*1024)
	for scanner.Scan() {
		var request struct {
			ID     json.RawMessage `json:"id"`
			Method string          `json:"method"`
			Params json.RawMessage `json:"params"`
		}
		if json.Unmarshal(scanner.Bytes(), &request) != nil || len(request.ID) == 0 {
			continue
		}
		var params struct {
			ThreadID string `json:"threadId"`
			Path     string `json:"path"`
			Input    []struct {
				Text string `json:"text"`
			} `json:"input"`
		}
		_ = json.Unmarshal(request.Params, &params)

		switch request.Method {
		case "initialize":
			reply(request.ID, map[string]any{"userAgent": "failover-helper"})
		case "account/read":
			account, ok := read("account.json")
			if !ok {
				account = "null"
			}
			write(map[string]any{"id": request.ID, "result": map[string]any{"account": json.RawMessage(account), "requiresOpenaiAuth": true}})
		case "account/rateLimits/read":
			limits, ok := read("ratelimits.json")
			if !ok {
				limits = "{}"
			}
			write(map[string]any{"id": request.ID, "result": map[string]any{"rateLimits": json.RawMessage(limits)}})
		case "thread/start":
			sequence++
			id := fmt.Sprintf("thr-%s-%d", label, sequence)
			path := filepath.Join(home, "sessions", "2026", "10", "05", "rollout-2026-10-05T00-00-00-"+id+".jsonl")
			if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
				fail(request.ID, -32603, err.Error())
				continue
			}
			if err := os.WriteFile(path, []byte("started on "+label+"\n"), 0o600); err != nil {
				fail(request.ID, -32603, err.Error())
				continue
			}
			threads[id] = path
			reply(request.ID, threadResult(id, path))
		case "thread/read":
			path, ok := threads[params.ThreadID]
			if !ok {
				fail(request.ID, -32600, "no rollout found for thread id "+params.ThreadID)
				continue
			}
			reply(request.ID, threadResult(params.ThreadID, path))
		case "thread/resume":
			if path, ok := threads[params.ThreadID]; ok {
				reply(request.ID, threadResult(params.ThreadID, path))
			} else if _, statErr := os.Stat(params.Path); params.Path != "" && insideHome(params.Path) && statErr == nil {
				threads[params.ThreadID] = params.Path
				reply(request.ID, threadResult(params.ThreadID, params.Path))
			} else {
				// A newer engine resolves the id against its own account-local
				// index, so a foreign rollout path is not enough.
				fail(request.ID, -32600, "no rollout found for thread id "+params.ThreadID)
			}
		case "thread/list":
			reply(request.ID, map[string]any{"data": []any{}, "nextCursor": nil})
		case "thread/archive", "thread/unarchive":
			reply(request.ID, map[string]any{})
		case "mcpServerStatus/list", "app/list":
			// Names the engine that answered and echoes what it was sent, so a test
			// can see which account served a scoped request and that the routing
			// marker never reached the engine's strict schema.
			reply(request.ID, map[string]any{
				"data":     []any{map[string]any{"name": label}},
				"received": string(request.Params),
			})
		case "turn/start":
			path, ok := threads[params.ThreadID]
			if !ok {
				fail(request.ID, -32600, "thread not found: "+params.ThreadID)
				continue
			}
			sequence++
			turnID := fmt.Sprintf("turn-%s-%d", label, sequence)
			if exists("exhausted-response") {
				fail(request.ID, -32603, "You've hit your usage limit.")
				continue
			}
			reply(request.ID, map[string]any{"turn": map[string]any{"id": turnID}})
			if exists("exhausted") {
				notify("error", map[string]any{
					"error":     map[string]any{"message": "You've hit your usage limit.", "codexErrorInfo": "usageLimitExceeded"},
					"willRetry": false,
					"threadId":  params.ThreadID,
					"turnId":    turnID,
				})
				continue
			}
			text := ""
			if len(params.Input) > 0 {
				text = params.Input[0].Text
			}
			file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
			if err == nil {
				fmt.Fprintf(file, "turn on %s: %s\n", label, text)
				file.Close()
			}
			notify("turn/completed", map[string]any{
				"threadId": params.ThreadID,
				"turn":     map[string]any{"id": turnID, "status": "completed"},
			})
		default:
			reply(request.ID, map[string]any{})
		}
	}
	os.Exit(0)
}

type syncBuffer struct {
	mu   sync.Mutex
	data bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.data.Write(p)
}

func (b *syncBuffer) messages() []protocol.Message {
	b.mu.Lock()
	defer b.mu.Unlock()
	var messages []protocol.Message
	for _, line := range strings.Split(b.data.String(), "\n") {
		var message protocol.Message
		if strings.TrimSpace(line) != "" && json.Unmarshal([]byte(line), &message) == nil {
			messages = append(messages, message)
		}
	}
	return messages
}

type failoverRig struct {
	t        *testing.T
	mux      *Multiplexer
	store    *state.Store
	primary  state.Account
	second   state.Account
	out      *syncBuffer
	sequence atomic.Int64
}

// newFailoverRig starts a multiplexer over two accounts whose engines are the
// scripted helper, and completes the client handshake.
func newFailoverRig(t *testing.T, primaryLimits, secondLimits string) *failoverRig {
	t.Helper()
	root := t.TempDir()
	primaryHome := filepath.Join(root, "primary")
	if err := os.MkdirAll(primaryHome, 0o700); err != nil {
		t.Fatal(err)
	}
	store, err := state.Open(filepath.Join(root, "mux"), primaryHome)
	if err != nil {
		t.Fatal(err)
	}
	primary := store.Accounts()[0]
	second, err := store.AddAccount("Subscription 2")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range []struct {
		account state.Account
		label   string
		limits  string
	}{{primary, "primary", primaryLimits}, {second, "second", secondLimits}} {
		for name, contents := range map[string]string{
			"account.json":    plusAccount,
			"ratelimits.json": entry.limits,
			"label":           entry.label,
		} {
			if err := os.WriteFile(filepath.Join(entry.account.CodexHome, name), []byte(contents), 0o600); err != nil {
				t.Fatal(err)
			}
		}
	}
	out := &syncBuffer{}
	multiplexer, err := New(Options{
		RealExecutable: os.Args[0],
		RealArgs:       []string{"-test.run=TestFailoverHelperProcess", "--"},
		Environment:    append(os.Environ(), "GO_WANT_FAILOVER_HELPER=1"),
		Store:          store,
		Output:         out,
	})
	if err != nil {
		t.Fatal(err)
	}
	// Keep reset-credit lookups off the network.
	multiplexer.resetCreditsEndpoint = "http://127.0.0.1:1/unused"
	ctx, cancel := context.WithCancel(context.Background())
	if err := multiplexer.Start(ctx); err != nil {
		cancel()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		cancel()
		multiplexer.Close()
	})
	rig := &failoverRig{t: t, mux: multiplexer, store: store, primary: primary, second: second, out: out}
	if response := rig.call("initialize", map[string]any{"clientInfo": map[string]any{"name": "test"}}); response.Error != nil {
		t.Fatalf("initialize failed: %v", response.Error)
	}
	multiplexer.HandleClient(protocol.Message{Method: "initialized"})
	return rig
}

func waitFor(t *testing.T, what string, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(20 * time.Second)
	for !condition() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// call sends one client request and returns the first response to it.
func (r *failoverRig) call(method string, params any) protocol.Message {
	r.t.Helper()
	id := fmt.Sprintf("client-%d", r.sequence.Add(1))
	encoded, err := json.Marshal(params)
	if err != nil {
		r.t.Fatal(err)
	}
	r.mux.HandleClient(protocol.Message{ID: protocol.StringID(id), Method: method, Params: encoded})
	var response protocol.Message
	waitFor(r.t, "the response to "+method, func() bool {
		for _, message := range r.out.messages() {
			if message.Method == "" && protocol.RequestIDKey(message.ID) == protocol.RequestIDKey(protocol.StringID(id)) {
				response = message
				return true
			}
		}
		return false
	})
	return response
}

func (r *failoverRig) startThread() string {
	r.t.Helper()
	response := r.call("thread/start", map[string]any{"cwd": "work"})
	if response.Error != nil {
		r.t.Fatalf("thread/start failed: %v", response.Error)
	}
	id := threadIDFromResult(response.Result)
	if id == "" {
		r.t.Fatalf("thread/start returned no thread: %s", response.Result)
	}
	return id
}

func (r *failoverRig) startTurn(threadID, text string) protocol.Message {
	r.t.Helper()
	return r.call("turn/start", map[string]any{
		"threadId": threadID,
		"input":    []map[string]any{{"type": "text", "text": text}},
	})
}

func (r *failoverRig) owner(threadID string) string {
	id, _ := r.store.ThreadOwner(threadID)
	return id
}

func (r *failoverRig) waitForOwner(threadID, accountID string) {
	r.t.Helper()
	waitFor(r.t, fmt.Sprintf("thread %s to be owned by %s", threadID, accountID), func() bool {
		return r.owner(threadID) == accountID
	})
}

func (r *failoverRig) waitForCompleted(threadID string) {
	r.t.Helper()
	waitFor(r.t, "turn/completed for "+threadID, func() bool {
		for _, message := range r.out.messages() {
			if message.Method == "turn/completed" && threadIDFromNotification(message.Params) == threadID {
				return true
			}
		}
		return false
	})
}

func (r *failoverRig) sawUsageLimitError() bool {
	for _, message := range r.out.messages() {
		if _, limited := usageLimitNotification(message); limited {
			return true
		}
	}
	return false
}

// history returns the rollout file of a thread in an account's home.
func (r *failoverRig) history(account state.Account, threadID string) string {
	r.t.Helper()
	var found string
	_ = filepath.Walk(filepath.Join(account.CodexHome, "sessions"), func(path string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() && strings.HasSuffix(info.Name(), "-"+threadID+".jsonl") {
			found = path
		}
		return nil
	})
	if found == "" {
		return ""
	}
	data, err := os.ReadFile(found)
	if err != nil {
		r.t.Fatal(err)
	}
	return string(data)
}

func (r *failoverRig) setLimits(account state.Account, limits string) {
	r.t.Helper()
	if err := os.WriteFile(filepath.Join(account.CodexHome, "ratelimits.json"), []byte(limits), 0o600); err != nil {
		r.t.Fatal(err)
	}
}

func (r *failoverRig) flag(account state.Account, name string) {
	r.t.Helper()
	if err := os.WriteFile(filepath.Join(account.CodexHome, name), []byte("1"), 0o600); err != nil {
		r.t.Fatal(err)
	}
}

// weeklySpentLimits has the shape a heavily used Pro account reports once its
// only (weekly) window is used up, as read from a live account: a single
// window at 100%, with a reset sooner than a fresh account's, so by urgency alone
// it would look like the better place to send work.
const weeklySpentLimits = `{"primary":{"usedPercent":100,"windowDurationMins":10080,"resetsAt":1791681784},"secondary":null,"rateLimitReachedType":"rate_limit_reached","planType":"prolite","credits":{"hasCredits":false,"unlimited":false,"balance":"0"}}`

func TestNewChatSkipsADepletedAccount(t *testing.T) {
	// Primary is spent, the second subscription is fresh: the situation of a
	// heavy user who has just connected a second plan.
	rig := newFailoverRig(t, weeklySpentLimits, healthyLimits)
	threadID := rig.startThread()
	if got := rig.owner(threadID); got != rig.second.ID {
		t.Fatalf("a new chat must go to the account with capacity: owner %q, want %q", got, rig.second.ID)
	}
	if rig.history(rig.primary, threadID) != "" {
		t.Fatal("the depleted account must not have been given the chat")
	}
}

func TestPinnedDepletedAccountFallsBackForANewChat(t *testing.T) {
	rig := newFailoverRig(t, depletedLimits, healthyLimits)
	if err := rig.mux.SetPreferredAccount(rig.primary.ID); err != nil {
		t.Fatal(err)
	}
	threadID := rig.startThread()
	if got := rig.owner(threadID); got != rig.second.ID {
		t.Fatalf("a pin on a spent account must fall back: owner %q, want %q", got, rig.second.ID)
	}
}

func TestPinnedHealthyAccountIsUsedAndFollowUpsStayOnIt(t *testing.T) {
	rig := newFailoverRig(t, healthyLimits, healthyLimits)
	if err := rig.mux.SetPreferredAccount(rig.second.ID); err != nil {
		t.Fatal(err)
	}
	threadID := rig.startThread()
	if got := rig.owner(threadID); got != rig.second.ID {
		t.Fatalf("a pinned account with capacity must be used: owner %q", got)
	}
	// Changing the pin afterwards must not move an existing chat.
	if err := rig.mux.SetPreferredAccount(rig.primary.ID); err != nil {
		t.Fatal(err)
	}
	for _, text := range []string{"first", "second"} {
		if response := rig.startTurn(threadID, text); response.Error != nil {
			t.Fatalf("turn failed: %v", response.Error)
		}
		rig.waitForCompleted(threadID)
	}
	if got := rig.owner(threadID); got != rig.second.ID {
		t.Fatalf("follow-ups must stay on the chat's account: owner %q", got)
	}
	history := rig.history(rig.second, threadID)
	if !strings.Contains(history, "turn on second: first") || !strings.Contains(history, "turn on second: second") {
		t.Fatalf("both turns must have run on the owning account:\n%s", history)
	}
	if rig.history(rig.primary, threadID) != "" {
		t.Fatal("the other account must not hold the chat")
	}
}

func TestTurnOnADepletedOwnerIsMovedBeforeItIsSent(t *testing.T) {
	rig := newFailoverRig(t, healthyLimits, healthyLimits)
	if err := rig.mux.SetPreferredAccount(rig.primary.ID); err != nil {
		t.Fatal(err)
	}
	threadID := rig.startThread()
	if rig.owner(threadID) != rig.primary.ID {
		t.Fatalf("setup: chat should start on primary, owner %q", rig.owner(threadID))
	}
	// The usage read now shows the owner spent, as it would the next day.
	rig.setLimits(rig.primary, depletedLimits)

	if response := rig.startTurn(threadID, "continue please"); response.Error != nil {
		t.Fatalf("the turn must be moved, not refused: %v", response.Error)
	}
	rig.waitForCompleted(threadID)
	rig.waitForOwner(threadID, rig.second.ID)

	history := rig.history(rig.second, threadID)
	if !strings.Contains(history, "started on primary") {
		t.Fatalf("the moved chat must keep its earlier history:\n%s", history)
	}
	if !strings.Contains(history, "turn on second: continue please") {
		t.Fatalf("the turn must have run on the account with capacity:\n%s", history)
	}
	if strings.Contains(rig.history(rig.primary, threadID), "continue please") {
		t.Fatal("the spent account must not have run the turn")
	}
}

func TestTurnKilledByAUsageLimitIsMovedAndReplayed(t *testing.T) {
	// The owner still reads as healthy (the usage figure is stale) but its
	// engine accepts the turn and then dies with a usage limit notification.
	rig := newFailoverRig(t, healthyLimits, healthyLimits)
	if err := rig.mux.SetPreferredAccount(rig.primary.ID); err != nil {
		t.Fatal(err)
	}
	threadID := rig.startThread()
	rig.flag(rig.primary, "exhausted")

	rig.startTurn(threadID, "replay me")
	rig.waitForCompleted(threadID)
	rig.waitForOwner(threadID, rig.second.ID)

	history := rig.history(rig.second, threadID)
	if !strings.Contains(history, "started on primary") || !strings.Contains(history, "turn on second: replay me") {
		t.Fatalf("history must be carried over and the turn replayed on the new account:\n%s", history)
	}
	if rig.sawUsageLimitError() {
		t.Fatal("the client must never see the usage limit when the turn was moved; it would end the turn in the UI")
	}
}

func TestRejectedTurnStartIsMovedAndReplayed(t *testing.T) {
	// Older engines reject turn/start itself with a usage limit error.
	rig := newFailoverRig(t, healthyLimits, healthyLimits)
	if err := rig.mux.SetPreferredAccount(rig.primary.ID); err != nil {
		t.Fatal(err)
	}
	threadID := rig.startThread()
	rig.flag(rig.primary, "exhausted-response")

	rig.startTurn(threadID, "after rejection")
	rig.waitForCompleted(threadID)
	rig.waitForOwner(threadID, rig.second.ID)
	if history := rig.history(rig.second, threadID); !strings.Contains(history, "turn on second: after rejection") {
		t.Fatalf("the rejected turn must run on the other account:\n%s", history)
	}
}

func TestEveryAccountDepletedIsReportedClearly(t *testing.T) {
	rig := newFailoverRig(t, depletedLimits, depletedLimits)
	response := rig.call("thread/start", map[string]any{"cwd": "work"})
	if response.Error == nil || !strings.Contains(response.Error.Message, "All connected subscriptions are depleted") {
		t.Fatalf("a new chat with nothing left must say so, got %#v", response.Error)
	}
}
