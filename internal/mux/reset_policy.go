package mux

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/b-nnett/codex-subscription-router/internal/state"
	"time"
)

func (m *Multiplexer) SetResetPolicy(policy string) error {
	m.resetAutoMu.Lock()
	defer m.resetAutoMu.Unlock()
	if err := m.store.SetResetPolicy(policy); err != nil {
		return err
	}
	m.publish(Event{Type: "routing-updated"})
	return nil
}

// Automatic redemption is opt-in and only reached after the pool is depleted.
// Serialize attempts and apply a cooldown so stale quota data cannot burn a
// sequence of credits. A manual reset remains available during the cooldown.
func (m *Multiplexer) tryAutomaticReset(ctx context.Context, accountID string) (spent, ready bool) {
	if m.store.ResetPolicy() != "auto" {
		return false, false
	}
	m.resetAutoMu.Lock()
	defer m.resetAutoMu.Unlock()
	if m.store.ResetPolicy() != "auto" {
		return false, false
	}
	if !m.resetAutoLast.IsZero() && time.Since(m.resetAutoLast) < 10*time.Minute {
		return false, false
	}
	// A previous request may already have restored another subscription.
	if _, _, err := m.chooseAccount(ctx); err != errNoSubscriptionCapacity {
		return false, false
	}
	if m.resetAutoAttempt == nil {
		m.resetAutoAttempt = map[string]time.Time{}
	}
	if last := m.resetAutoAttempt[accountID]; !last.IsZero() && time.Since(last) < 10*time.Minute {
		return false, false
	}
	snapshot, err := m.accountSnapshotWithProfile(ctx, accountID, false)
	if err != nil || !snapshot.Enabled || !snapshot.Connected || snapshot.NeedsReauth || snapshot.AuthType != "chatgpt" || accountHasCapacity(snapshot) {
		return false, false
	}
	m.resetAutoAttempt[accountID] = time.Now()
	spent, ready = redeemAutomaticReset(ctx, accountID, m.store, m.RateLimitResetCredits, m.ConsumeRateLimitResetCredit, func() bool {
		m.forgetAccountCaches(accountID)
		fresh, err := m.accountSnapshot(ctx, accountID)
		return err == nil && accountHasCapacity(fresh)
	})
	if spent {
		m.resetAutoLast = time.Now()
	}
	return spent, ready
}

func redeemAutomaticReset(ctx context.Context, id string, store *state.Store, credits func(context.Context, string) (json.RawMessage, error), consume func(context.Context, string, *string, string) (json.RawMessage, error), capacity func() bool) (spent, ready bool) {
	if store.ResetPolicy() != "auto" {
		return false, false
	}
	payload, err := credits(ctx, id)
	if err != nil {
		return false, false
	}
	var response struct {
		Credits []struct {
			ID        string `json:"id"`
			Status    string `json:"status"`
			Supported bool   `json:"is_supported_by_plan"`
			Expires   string `json:"expires_at"`
		} `json:"credits"`
	}
	if json.Unmarshal(payload, &response) != nil {
		return false, false
	}
	var selected string
	var earliest time.Time
	for _, credit := range response.Credits {
		expiry, err := time.Parse(time.RFC3339, credit.Expires)
		if credit.Status != "available" || !credit.Supported || credit.ID == "" || err != nil || !expiry.After(time.Now()) {
			continue
		}
		if selected == "" || expiry.Before(earliest) {
			selected = credit.ID
			earliest = expiry
		}
	}
	if selected == "" || store.ResetPolicy() != "auto" {
		return false, false
	}
	// Once a consume request is issued, its outcome can be uncertain.
	// Stop pool spending even if the response is lost after redemption.
	result, err := consume(ctx, id, &selected, fmt.Sprintf("router-auto-%d", time.Now().UnixNano()))
	if err != nil {
		return true, false
	}
	var redeemed struct {
		Code string `json:"code"`
	}
	if json.Unmarshal(result, &redeemed) != nil || redeemed.Code != "reset" {
		return true, false
	}
	return true, capacity()
}
