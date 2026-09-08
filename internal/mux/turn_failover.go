package mux

import (
	"context"
	"encoding/json"
	"strings"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

// A usage limit does not always arrive as an error response to turn/start.
// On current desktop builds turn/start is accepted and answered successfully,
// and the turn then fails inside the engine's session loop. The failure
// reaches the router as an "error" notification carrying the thread and turn
// it killed:
//
//	{"method":"error","params":{
//	   "error":{"message":"You've hit your usage limit...",
//	            "codexErrorInfo":"usageLimitExceeded"},
//	   "willRetry":false,
//	   "threadId":"...","turnId":"..."}}
//
// A notification has no request id, so it never reaches the response path and
// the recorded turn/start route is already consumed. Without this the chat
// dies on a spent account while another sits unused.
type usageLimitEvent struct {
	Error struct {
		Message        string `json:"message"`
		CodexErrorInfo string `json:"codexErrorInfo"`
	} `json:"error"`
	WillRetry bool   `json:"willRetry"`
	ThreadID  string `json:"threadId"`
	TurnID    string `json:"turnId"`
}

// usageLimitNotification reports the thread a usage limit just killed.
// It returns false when the engine intends to retry by itself, so the router
// never competes with the engine's own recovery.
func usageLimitNotification(message protocol.Message) (string, bool) {
	if message.Method != "error" || len(message.Params) == 0 {
		return "", false
	}
	var event usageLimitEvent
	if err := json.Unmarshal(message.Params, &event); err != nil {
		return "", false
	}
	if event.WillRetry || event.ThreadID == "" {
		return "", false
	}
	if strings.EqualFold(event.Error.CodexErrorInfo, "usageLimitExceeded") {
		return event.ThreadID, true
	}
	// The machine-readable code is authoritative; the text is a fallback for
	// builds that word it differently.
	text := strings.ToLower(event.Error.Message)
	for _, pattern := range []string{"usage limit", "usage_limit", "rate limit", "rate_limit", "quota"} {
		if strings.Contains(text, pattern) {
			return event.ThreadID, true
		}
	}
	return "", false
}

// rememberInflightTurn keeps a turn/start so it can be replayed on another
// account. The response to turn/start arrives long before the turn finishes,
// so the route that carried it is gone by the time the turn fails.
func (m *Multiplexer) rememberInflightTurn(message protocol.Message) {
	if message.Method != "turn/start" {
		return
	}
	threadID := threadIDFromParams(message.Params)
	if threadID == "" {
		return
	}
	m.inflightMu.Lock()
	m.inflightTurns[threadID] = message
	m.inflightMu.Unlock()
}

func (m *Multiplexer) takeInflightTurn(threadID string) (protocol.Message, bool) {
	m.inflightMu.Lock()
	defer m.inflightMu.Unlock()
	message, ok := m.inflightTurns[threadID]
	if ok {
		delete(m.inflightTurns, threadID)
	}
	return message, ok
}

func (m *Multiplexer) forgetInflightTurn(threadID string) {
	if threadID == "" {
		return
	}
	m.inflightMu.Lock()
	delete(m.inflightTurns, threadID)
	m.inflightMu.Unlock()
}

// failOverKilledTurn moves a thread whose turn died on a spent account onto one
// with capacity and replays the turn there. It reports whether it took
// ownership of the notification, so the caller can withhold the usage-limit
// error from the client while the retry is in flight.
func (m *Multiplexer) failOverKilledTurn(threadID, exhaustedAccountID string) bool {
	message, ok := m.takeInflightTurn(threadID)
	if !ok {
		return false
	}
	excluded := map[string]struct{}{exhaustedAccountID: {}}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*requestTimeout)
		defer cancel()
		m.failoverTurn(ctx, message, threadID, exhaustedAccountID, excluded)
	}()
	return true
}
