package mux

import (
	"encoding/json"
	"strings"
	"testing"
)

// These tests send the account-scoped plugin requests the desktop app's
// Plugins page makes (a `codexMuxAccountId` marker on the app and MCP status
// calls) through the real multiplexer to two scripted engines, and check which
// engine answered.

type scopedAnswer struct {
	Engine   string
	Received string
}

// scopedList returns the answer, or the error text when the request failed.
func (r *failoverRig) scopedList(method string, params map[string]any) (scopedAnswer, string) {
	r.t.Helper()
	response := r.call(method, params)
	if response.Error != nil {
		return scopedAnswer{}, response.Error.Message
	}
	var result struct {
		Data []struct {
			Name string `json:"name"`
		} `json:"data"`
		Received string `json:"received"`
	}
	if err := json.Unmarshal(response.Result, &result); err != nil || len(result.Data) != 1 {
		r.t.Fatalf("unexpected %s result: %s", method, response.Result)
	}
	return scopedAnswer{Engine: result.Data[0].Name, Received: result.Received}, ""
}

func TestScopedPluginRequestsAreAnsweredByTheNamedAccount(t *testing.T) {
	rig := newFailoverRig(t, healthyLimits, healthyLimits)
	for _, method := range []string{"mcpServerStatus/list", "app/list"} {
		secondAnswer, failure := rig.scopedList(method, map[string]any{"codexMuxAccountId": rig.second.ID})
		if failure != "" || secondAnswer.Engine != "second" {
			t.Fatalf("%s scoped to the second account was answered by %q (%s)", method, secondAnswer.Engine, failure)
		}
		primaryAnswer, failure := rig.scopedList(method, map[string]any{"codexMuxAccountId": rig.primary.ID})
		if failure != "" || primaryAnswer.Engine != "primary" {
			t.Fatalf("%s scoped to the primary account was answered by %q (%s)", method, primaryAnswer.Engine, failure)
		}
		// The marker is the router's own extension; the engine's strict schema
		// must never see it.
		for _, answer := range []scopedAnswer{secondAnswer, primaryAnswer} {
			if strings.Contains(answer.Received, "codexMuxAccountId") {
				t.Fatalf("%s forwarded the routing marker to an engine: %s", method, answer.Received)
			}
		}
		// Without a marker the request belongs to the controller (Primary).
		plain, failure := rig.scopedList(method, map[string]any{})
		if failure != "" || plain.Engine != "primary" {
			t.Fatalf("an unscoped %s was answered by %q (%s)", method, plain.Engine, failure)
		}
	}
}

func TestScopedPluginRequestForAnUnavailableAccountFailsClosed(t *testing.T) {
	rig := newFailoverRig(t, healthyLimits, healthyLimits)

	// An account that does not exist (removed while a picker still held its id)
	// must not be answered by Primary: an OAuth login or a connection list for
	// the wrong account is worse than an error.
	for _, method := range []string{"mcpServerStatus/list", "mcpServer/oauth/login"} {
		answer, failure := rig.scopedList(method, map[string]any{"codexMuxAccountId": "no-such-account", "name": "github"})
		if failure == "" {
			t.Fatalf("%s for an unknown account was answered by %q instead of failing", method, answer.Engine)
		}
		if !strings.Contains(failure, "not available") {
			t.Fatalf("unexpected failure for %s: %s", method, failure)
		}
	}

	// A paused account is excluded the same way.
	paused := false
	if _, err := rig.store.UpdateAccount(rig.second.ID, nil, &paused); err != nil {
		t.Fatal(err)
	}
	answer, failure := rig.scopedList("mcpServerStatus/list", map[string]any{"codexMuxAccountId": rig.second.ID})
	if failure == "" {
		t.Fatalf("a request for a paused account was answered by %q instead of failing", answer.Engine)
	}

	// Primary keeps answering its own scoped requests.
	if answer, failure := rig.scopedList("mcpServerStatus/list", map[string]any{"codexMuxAccountId": rig.primary.ID}); failure != "" || answer.Engine != "primary" {
		t.Fatalf("the primary account stopped answering: %q (%s)", answer.Engine, failure)
	}
}
