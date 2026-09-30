package mux

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/b-nnett/codex-subscription-router/internal/state"
	"path/filepath"
	"testing"
)

func TestAutomaticResetRequiresOptInAndChoosesSupportedEarliestCredit(t *testing.T) {
	store, err := state.Open(t.TempDir(), filepath.Join(t.TempDir(), "home"))
	if err != nil {
		t.Fatal(err)
	}
	reads, spent := 0, 0
	credits := func(context.Context, string) (json.RawMessage, error) {
		reads++
		return json.RawMessage(`{"credits":[{"id":"unsupported","status":"available","is_supported_by_plan":false,"expires_at":"2090-01-01T00:00:00Z"},{"id":"later","status":"available","is_supported_by_plan":true,"expires_at":"2099-01-01T00:00:00Z"},{"id":"earlier","status":"available","is_supported_by_plan":true,"expires_at":"2095-01-01T00:00:00Z"}]}`), nil
	}
	consume := func(_ context.Context, id string, credit *string, request string) (json.RawMessage, error) {
		spent++
		if id != "primary" || *credit != "earlier" || request == "" {
			t.Fatalf("wrong redemption %s %v", id, credit)
		}
		return json.RawMessage(`{"code":"reset"}`), nil
	}
	if used, _ := redeemAutomaticReset(context.Background(), "primary", store, credits, consume, func() bool { return true }); used || reads != 0 || spent != 0 {
		t.Fatal("default mode accessed/spent resets")
	}
	store.SetResetPolicy("auto")
	if used, ready := redeemAutomaticReset(context.Background(), "primary", store, credits, consume, func() bool { return true }); !used || !ready || spent != 1 {
		t.Fatal("opted-in redemption failed")
	}
	store.SetResetPolicy("ask")
	if used, _ := redeemAutomaticReset(context.Background(), "primary", store, credits, consume, func() bool { return true }); used || spent != 1 {
		t.Fatal("switching to ask did not stop redemption")
	}
}

func TestAutomaticResetReportsSpentWhenUsageRefreshIsPending(t *testing.T) {
	store, err := state.Open(t.TempDir(), filepath.Join(t.TempDir(), "home"))
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SetResetPolicy("auto"); err != nil {
		t.Fatal(err)
	}
	spent, ready := redeemAutomaticReset(context.Background(), "primary", store,
		func(context.Context, string) (json.RawMessage, error) {
			return json.RawMessage(`{"credits":[{"id":"one","status":"available","is_supported_by_plan":true,"expires_at":"2099-01-01T00:00:00Z"}]}`), nil
		},
		func(context.Context, string, *string, string) (json.RawMessage, error) {
			return json.RawMessage(`{"code":"reset"}`), nil
		},
		func() bool { return false })
	if !spent || ready {
		t.Fatal("successful spending must stop further resets even when quota remains stale")
	}
}

func TestAutomaticResetRejectsIneligibleCredits(t *testing.T) {
	store, err := state.Open(t.TempDir(), filepath.Join(t.TempDir(), "home"))
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SetResetPolicy("auto"); err != nil {
		t.Fatal(err)
	}
	for _, credit := range []string{
		`{"id":"expired","status":"available","is_supported_by_plan":true,"expires_at":"2000-01-01T00:00:00Z"}`,
		`{"id":"unsupported","status":"available","is_supported_by_plan":false,"expires_at":"2099-01-01T00:00:00Z"}`,
		`{"id":"consumed","status":"consumed","is_supported_by_plan":true,"expires_at":"2099-01-01T00:00:00Z"}`,
		`{"id":"invalid-expiry","status":"available","is_supported_by_plan":true,"expires_at":"invalid"}`,
		`{"status":"available","is_supported_by_plan":true,"expires_at":"2099-01-01T00:00:00Z"}`,
	} {
		used, ready := redeemAutomaticReset(context.Background(), "primary", store,
			func(context.Context, string) (json.RawMessage, error) {
				return json.RawMessage(`{"credits":[` + credit + `]}`), nil
			},
			func(context.Context, string, *string, string) (json.RawMessage, error) {
				t.Fatal("ineligible credit was consumed")
				return nil, nil
			},
			func() bool { t.Fatal("capacity checked without redemption"); return false })
		if used || ready {
			t.Fatalf("accepted ineligible credit: %s", credit)
		}
	}
}

func TestAutomaticResetRechecksPolicyAfterFetchingCredits(t *testing.T) {
	store, err := state.Open(t.TempDir(), filepath.Join(t.TempDir(), "home"))
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SetResetPolicy("auto"); err != nil {
		t.Fatal(err)
	}
	used, ready := redeemAutomaticReset(context.Background(), "primary", store,
		func(context.Context, string) (json.RawMessage, error) {
			if err := store.SetResetPolicy("ask"); err != nil {
				t.Fatal(err)
			}
			return json.RawMessage(`{"credits":[{"id":"fake","status":"available","is_supported_by_plan":true,"expires_at":"2099-01-01T00:00:00Z"}]}`), nil
		},
		func(context.Context, string, *string, string) (json.RawMessage, error) {
			t.Fatal("policy change did not prevent consumption")
			return nil, nil
		},
		func() bool { return true })
	if used || ready {
		t.Fatal("ask policy permitted automatic redemption")
	}
}

func TestAutomaticResetStopsAfterUncertainRedemption(t *testing.T) {
	store, err := state.Open(t.TempDir(), filepath.Join(t.TempDir(), "home"))
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SetResetPolicy("auto"); err != nil {
		t.Fatal(err)
	}
	for _, outcome := range []string{"transport-error", "malformed", "non-reset"} {
		t.Run(outcome, func(t *testing.T) {
			attempts := 0
			used, ready := redeemAutomaticReset(context.Background(), "primary", store,
				func(context.Context, string) (json.RawMessage, error) {
					return json.RawMessage(`{"credits":[{"id":"fake","status":"available","is_supported_by_plan":true,"expires_at":"2099-01-01T00:00:00Z"}]}`), nil
				},
				func(context.Context, string, *string, string) (json.RawMessage, error) {
					attempts++
					switch outcome {
					case "transport-error":
						return nil, errors.New("response lost after request")
					case "malformed":
						return json.RawMessage(`invalid`), nil
					default:
						return json.RawMessage(`{"code":"already_redeemed"}`), nil
					}
				},
				func() bool { t.Fatal("uncertain result must not report restored capacity"); return true })
			if !used || ready || attempts != 1 {
				t.Fatalf("uncertain attempt must stop further spending: used=%v ready=%v attempts=%d", used, ready, attempts)
			}
		})
	}
}
