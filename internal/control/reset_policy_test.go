package control

import (
	"bytes"
	"context"
	"github.com/b-nnett/codex-subscription-router/internal/mux"
	"github.com/b-nnett/codex-subscription-router/internal/state"
	"io"
	"net/http/httptest"
	"path/filepath"
	"testing"
)

func TestResetAPIRequiresConfirmationInAskMode(t *testing.T) {
	store, err := state.Open(t.TempDir(), filepath.Join(t.TempDir(), "home"))
	if err != nil {
		t.Fatal(err)
	}
	m, err := mux.New(mux.Options{RealExecutable: "unused", Store: store, Output: io.Discard})
	if err != nil {
		t.Fatal(err)
	}
	m.SetResetCreditsPreview(mux.ResetCreditsPreview{AccountID: "primary", AvailableCount: 2})
	server := New("127.0.0.1:0", "test-token", m, true)
	for _, confirmed := range []bool{false, true} {
		body := `{"redeemRequestId":"test","confirmed":false}`
		if confirmed {
			body = `{"redeemRequestId":"test","confirmed":true}`
		}
		req := httptest.NewRequest("POST", "/v1/accounts/primary/rate-limit-resets/consume", bytes.NewBufferString(body))
		req.Header.Set("X-Codex-Mux-Token", "test-token")
		res := httptest.NewRecorder()
		server.http.Handler.ServeHTTP(res, req)
		expected := 409
		if confirmed {
			expected = 200
		}
		if res.Code != expected {
			t.Fatalf("confirmed=%v status=%d body=%s", confirmed, res.Code, res.Body.String())
		}
	}
	result, err := m.RateLimitResetCredits(context.Background(), "primary")
	if err != nil || !bytes.Contains(result, []byte(`"available_count":1`)) {
		t.Fatalf("unexpected spend: %s %v", result, err)
	}
}
