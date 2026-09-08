package mux

import (
	"encoding/json"
	"os"
	"strings"
	"sync"
	"time"
)

// Protocol drift on a new desktop build is otherwise invisible: a message the
// router does not recognise is simply forwarded, and the behaviour that
// depended on recognising it stops without any error. Mid-conversation
// failover was unreachable for exactly that reason, and finding it needed an
// account's own engine log.
//
// CODEX_MUX_TRACE_FILE appends the raw JSON of inbound messages whose method
// is in CODEX_MUX_TRACE_METHODS (comma separated, default "error"). It is off
// unless the file is set. Traced payloads can quote model or user text, so it
// writes only the methods asked for and never defaults to everything.

var (
	traceOnce    sync.Once
	traceMethods map[string]struct{}
	traceMu      sync.Mutex
	tracePath    string
)

func traceConfig() (string, map[string]struct{}) {
	traceOnce.Do(func() {
		tracePath = strings.TrimSpace(os.Getenv("CODEX_MUX_TRACE_FILE"))
		configured := strings.TrimSpace(os.Getenv("CODEX_MUX_TRACE_METHODS"))
		if configured == "" {
			configured = "error"
		}
		traceMethods = make(map[string]struct{})
		for _, method := range strings.Split(configured, ",") {
			if method = strings.TrimSpace(method); method != "" {
				traceMethods[method] = struct{}{}
			}
		}
	})
	return tracePath, traceMethods
}

// traceInboundMessage records one raw inbound message when tracing is enabled
// and its method was asked for. Every failure is ignored: a diagnostic must
// never change how the router behaves.
func traceInboundMessage(accountID, method string, raw []byte) {
	path, methods := traceConfig()
	if path == "" || method == "" {
		return
	}
	if _, wanted := methods[method]; !wanted {
		return
	}
	entry, err := json.Marshal(map[string]any{
		"at":        time.Now().UTC().Format(time.RFC3339Nano),
		"accountId": accountID,
		"method":    method,
		"raw":       json.RawMessage(raw),
	})
	if err != nil {
		return
	}
	traceMu.Lock()
	defer traceMu.Unlock()
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return
	}
	defer file.Close()
	_, _ = file.Write(append(entry, '\n'))
}
