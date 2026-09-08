package mux

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func resetTraceConfig() {
	traceOnce = sync.Once{}
	traceMethods = nil
	tracePath = ""
}

func TestTraceIsOffWithoutAFile(t *testing.T) {
	resetTraceConfig()
	t.Setenv("CODEX_MUX_TRACE_FILE", "")
	// Must not panic and must not create anything.
	traceInboundMessage("primary", "error", []byte(`{"method":"error"}`))
}

func TestTraceRecordsOnlyRequestedMethods(t *testing.T) {
	resetTraceConfig()
	path := filepath.Join(t.TempDir(), "trace.jsonl")
	t.Setenv("CODEX_MUX_TRACE_FILE", path)
	t.Setenv("CODEX_MUX_TRACE_METHODS", "error")

	traceInboundMessage("acct", "error", []byte(`{"method":"error","params":{"message":"limit"}}`))
	traceInboundMessage("acct", "turn/completed", []byte(`{"method":"turn/completed"}`))

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var entry struct {
		AccountID string          `json:"accountId"`
		Method    string          `json:"method"`
		Raw       json.RawMessage `json:"raw"`
	}
	if err := json.Unmarshal(data[:len(data)-1], &entry); err != nil {
		t.Fatalf("one line expected, got %q: %v", data, err)
	}
	if entry.Method != "error" || entry.AccountID != "acct" {
		t.Fatalf("unexpected entry: %#v", entry)
	}
	if len(entry.Raw) == 0 {
		t.Fatal("the raw message must be preserved verbatim")
	}
}

func TestTraceDefaultsToErrorOnly(t *testing.T) {
	resetTraceConfig()
	path := filepath.Join(t.TempDir(), "trace.jsonl")
	t.Setenv("CODEX_MUX_TRACE_FILE", path)

	traceInboundMessage("acct", "thread/started", []byte(`{"method":"thread/started"}`))
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("an unrequested method must not be written, and must not create the file")
	}
	traceInboundMessage("acct", "error", []byte(`{"method":"error"}`))
	if _, err := os.Stat(path); err != nil {
		t.Fatal("the default method set must include error")
	}
}

func TestTraceIgnoresAnUnwritableTarget(t *testing.T) {
	resetTraceConfig()
	t.Setenv("CODEX_MUX_TRACE_FILE", filepath.Join(t.TempDir(), "missing", "trace.jsonl"))
	// A diagnostic must never change routing behaviour, so this cannot panic.
	traceInboundMessage("acct", "error", []byte(`{"method":"error"}`))
}
