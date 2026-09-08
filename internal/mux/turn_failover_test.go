package mux

import (
	"encoding/json"
	"testing"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

func errorNotification(body string) protocol.Message {
	return protocol.Message{Method: "error", Params: json.RawMessage(body)}
}

// The exact payload captured from a desktop build when a turn died on a spent
// account. turn/start had already been answered successfully.
const capturedUsageLimit = `{
  "error": {
    "message": "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 12:28 PM.",
    "codexErrorInfo": "usageLimitExceeded",
    "additionalDetails": null,
    "misalignment": null
  },
  "willRetry": false,
  "threadId": "01a080e7-a29e-73c2-a944-ba2f6eafa5a5",
  "turnId": "01a080f0-e393-78a2-8132-eec70a63efa0"
}`

func TestCapturedUsageLimitIsRecognised(t *testing.T) {
	threadID, ok := usageLimitNotification(errorNotification(capturedUsageLimit))
	if !ok {
		t.Fatal("the captured payload must be recognised as a usage limit")
	}
	if threadID != "01a080e7-a29e-73c2-a944-ba2f6eafa5a5" {
		t.Fatalf("wrong thread: %q", threadID)
	}
}

func TestEngineRetryIsLeftAlone(t *testing.T) {
	body := `{"error":{"codexErrorInfo":"usageLimitExceeded"},"willRetry":true,"threadId":"t1"}`
	if _, ok := usageLimitNotification(errorNotification(body)); ok {
		t.Fatal("the router must not compete with the engine's own retry")
	}
}

func TestUnrelatedErrorsAreNotFailedOver(t *testing.T) {
	for _, body := range []string{
		`{"error":{"message":"workspace folder is unavailable"},"willRetry":false,"threadId":"t1"}`,
		`{"error":{"codexErrorInfo":"usageLimitExceeded"},"willRetry":false}`,
		`{"error":{"message":"usage limit"},"willRetry":false,"threadId":""}`,
	} {
		if _, ok := usageLimitNotification(errorNotification(body)); ok {
			t.Fatalf("must not fail over: %s", body)
		}
	}
}

func TestOnlyErrorNotificationsAreConsidered(t *testing.T) {
	if _, ok := usageLimitNotification(protocol.Message{
		Method: "turn/completed",
		Params: json.RawMessage(capturedUsageLimit),
	}); ok {
		t.Fatal("only the error notification carries a killed turn")
	}
	if _, ok := usageLimitNotification(protocol.Message{Method: "error"}); ok {
		t.Fatal("an error notification without params is not a usage limit")
	}
	if _, ok := usageLimitNotification(errorNotification(`{"error":`)); ok {
		t.Fatal("malformed params must not be treated as a usage limit")
	}
}

func TestWordedDifferentlyStillFailsOver(t *testing.T) {
	body := `{"error":{"message":"You have reached your rate limit."},"willRetry":false,"threadId":"t9"}`
	threadID, ok := usageLimitNotification(errorNotification(body))
	if !ok || threadID != "t9" {
		t.Fatal("a build wording it differently must still fail over")
	}
}

func TestInflightTurnIsRememberedAndTakenOnce(t *testing.T) {
	m := &Multiplexer{inflightTurns: make(map[string]protocol.Message)}
	turn := protocol.Message{
		Method: "turn/start",
		Params: json.RawMessage(`{"threadId":"t1"}`),
	}
	m.rememberInflightTurn(turn)
	if _, ok := m.takeInflightTurn("t1"); !ok {
		t.Fatal("the turn must be replayable")
	}
	if _, ok := m.takeInflightTurn("t1"); ok {
		t.Fatal("taking it twice would replay the same turn twice")
	}
}

func TestOnlyTurnStartsAreRemembered(t *testing.T) {
	m := &Multiplexer{inflightTurns: make(map[string]protocol.Message)}
	m.rememberInflightTurn(protocol.Message{
		Method: "thread/read",
		Params: json.RawMessage(`{"threadId":"t1"}`),
	})
	if _, ok := m.takeInflightTurn("t1"); ok {
		t.Fatal("only a turn/start can be replayed")
	}
}

func TestCompletedTurnIsForgotten(t *testing.T) {
	m := &Multiplexer{inflightTurns: make(map[string]protocol.Message)}
	m.rememberInflightTurn(protocol.Message{
		Method: "turn/start",
		Params: json.RawMessage(`{"threadId":"t1"}`),
	})
	m.forgetInflightTurn("t1")
	if _, ok := m.takeInflightTurn("t1"); ok {
		t.Fatal("a finished turn must not be replayed by a later error")
	}
}

func TestAnUntrackedThreadIsNotClaimed(t *testing.T) {
	m := &Multiplexer{inflightTurns: make(map[string]protocol.Message)}
	if m.failOverKilledTurn("unknown", "primary") {
		t.Fatal("with no turn to replay the error must reach the client")
	}
}
