package mux

import (
	"context"
	"encoding/json"
	"time"
)

// tokenRefreshInterval bounds how often the router asks an app-server to
// refresh its ChatGPT token on its own initiative. Refreshes rotate the
// refresh token, so they must stay rare and must always go through the
// account's own app-server, which is the only process that owns its auth.json.
const tokenRefreshInterval = 15 * time.Minute

// scheduleTokenRefresh refreshes an account's token in the background when the
// router notices a stale plan or a rejected access token.
func (m *Multiplexer) scheduleTokenRefresh(accountID string) {
	if !m.claimTokenRefresh(accountID) {
		return
	}
	go func() {
		child, ok := m.child(accountID)
		if !ok {
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
		defer cancel()
		if _, err := child.Request(ctx, "account/read", json.RawMessage(`{"refreshToken":true}`)); err != nil {
			return
		}
		m.forgetAccountCaches(accountID)
		m.publishAccountRefresh(accountID)
	}()
}

func (m *Multiplexer) claimTokenRefresh(accountID string) bool {
	m.tokenRefreshMu.Lock()
	defer m.tokenRefreshMu.Unlock()
	if m.tokenRefreshAt == nil {
		m.tokenRefreshAt = make(map[string]time.Time)
	}
	now := m.now()
	if last, ok := m.tokenRefreshAt[accountID]; ok && now.Sub(last) < tokenRefreshInterval {
		return false
	}
	m.tokenRefreshAt[accountID] = now
	return true
}

func (m *Multiplexer) markTokenRefreshed(accountID string) {
	m.tokenRefreshMu.Lock()
	if m.tokenRefreshAt == nil {
		m.tokenRefreshAt = make(map[string]time.Time)
	}
	m.tokenRefreshAt[accountID] = m.now()
	m.tokenRefreshMu.Unlock()
}
