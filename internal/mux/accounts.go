package mux

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/state"
)

var errNoSubscriptionCapacity = errors.New("no enabled ChatGPT subscription has capacity")

const (
	routingFallbackWindow      = 7 * 24 * time.Hour
	routingMinimumWindow       = time.Minute
	routingResetBonusPerCredit = 0.15
	routingResetBonusCreditCap = 3
)

type RateLimitWindow struct {
	UsedPercent        float64 `json:"usedPercent"`
	WindowDurationMins *int64  `json:"windowDurationMins"`
	ResetsAt           *int64  `json:"resetsAt"`
}

type RateLimits struct {
	Primary              *RateLimitWindow `json:"primary"`
	Secondary            *RateLimitWindow `json:"secondary"`
	RateLimitReachedType any              `json:"rateLimitReachedType"`
	// PlanType and Credits come from the live usage endpoint, unlike the plan
	// in account/read, which is decoded from the cached ID token and stays
	// stale after an upgrade until that token is refreshed.
	PlanType string          `json:"planType,omitempty"`
	Credits  *CreditsBalance `json:"credits,omitempty"`
}

type CreditsBalance struct {
	HasCredits bool    `json:"hasCredits"`
	Unlimited  bool    `json:"unlimited"`
	Balance    *string `json:"balance,omitempty"`
}

type AccountSnapshot struct {
	ID              string          `json:"id"`
	Label           string          `json:"label"`
	Enabled         bool            `json:"enabled"`
	Controller      bool            `json:"controller"`
	Connected       bool            `json:"connected"`
	Email           string          `json:"email,omitempty"`
	PlanType        string          `json:"planType,omitempty"`
	PlanLabel       string          `json:"planLabel,omitempty"`
	AuthType        string          `json:"authType,omitempty"`
	ProfileImageURL string          `json:"profileImageUrl,omitempty"`
	RateLimits      *RateLimits     `json:"rateLimits,omitempty"`
	Credits         *CreditsBalance `json:"credits,omitempty"`
	// NeedsReauth means the saved sign-in was rejected by ChatGPT (for
	// example after a plan change or a sign-out elsewhere). The account is
	// kept out of routing until it signs in again.
	NeedsReauth bool            `json:"needsReauth,omitempty"`
	UsageError  string          `json:"usageError,omitempty"`
	Preferred   bool            `json:"preferred"`
	ThreadCount int             `json:"threadCount"`
	Error       string          `json:"error,omitempty"`
	CreatedAt   int64           `json:"createdAt"`
	RawAccount  json.RawMessage `json:"-"`
}

type RouteReason struct {
	WeeklyUsedPercent    *float64 `json:"weeklyUsedPercent"`
	WeeklyResetsAt       *int64   `json:"weeklyResetsAt,omitempty"`
	ShortUsedPercent     *float64 `json:"shortUsedPercent"`
	BankedResetCount     *int     `json:"bankedResetCount,omitempty"`
	ResetCreditExpiresAt *int64   `json:"resetCreditExpiresAt,omitempty"`
	UrgencyScore         *float64 `json:"urgencyScore,omitempty"`
	ThreadCount          int      `json:"threadCount"`
	Preferred            bool     `json:"preferred,omitempty"`
}

func (m *Multiplexer) Accounts(ctx context.Context) []AccountSnapshot {
	return m.accountSnapshots(ctx, true)
}

func (m *Multiplexer) accountSnapshots(ctx context.Context, includeProfile bool) []AccountSnapshot {
	accounts := m.store.Accounts()
	threadCounts := m.store.ThreadCounts()
	preferred := m.store.PreferredAccount()
	sort.SliceStable(accounts, func(i, j int) bool {
		if accounts[i].Controller != accounts[j].Controller {
			return accounts[i].Controller
		}
		return accounts[i].CreatedAt < accounts[j].CreatedAt
	})

	type snapshotResult struct {
		index    int
		snapshot AccountSnapshot
	}
	results := make(chan snapshotResult, len(accounts))
	snapshots := make([]AccountSnapshot, len(accounts))
	completed := make([]bool, len(accounts))
	for index, account := range accounts {
		snapshots[index] = AccountSnapshot{
			ID: account.ID, Label: account.Label, Enabled: account.Enabled,
			Controller: account.Controller, CreatedAt: account.CreatedAt,
			ThreadCount: threadCounts[account.ID], Preferred: account.ID == preferred,
		}
		go func(index int, account state.Account) {
			snapshot, err := m.accountSnapshotWithProfile(ctx, account.ID, includeProfile)
			if err != nil {
				snapshot = AccountSnapshot{
					ID: account.ID, Label: account.Label, Enabled: account.Enabled,
					Controller: account.Controller, CreatedAt: account.CreatedAt,
					ThreadCount: threadCounts[account.ID], Error: err.Error(),
					Preferred: account.ID == preferred,
				}
			}
			results <- snapshotResult{index: index, snapshot: snapshot}
		}(index, account)
	}
	for range accounts {
		select {
		case result := <-results:
			snapshots[result.index] = result.snapshot
			completed[result.index] = true
		case <-ctx.Done():
			for index := range snapshots {
				if !completed[index] {
					snapshots[index].Error = ctx.Err().Error()
				}
			}
			return snapshots
		}
	}
	return snapshots
}

func (m *Multiplexer) AddAccount(ctx context.Context, label string) (AccountSnapshot, error) {
	// Reuse a slot whose sign-in was never finished instead of piling up
	// empty accounts (and idle app-server processes) on every retry.
	if reusable, ok := m.unsignedAccount(ctx); ok {
		return reusable, nil
	}
	account, err := m.store.AddAccount(label)
	if err != nil {
		return AccountSnapshot{}, err
	}
	if _, err := m.startChild(ctx, account); err != nil {
		return AccountSnapshot{}, err
	}
	return m.accountSnapshot(ctx, account.ID)
}

func (m *Multiplexer) unsignedAccount(ctx context.Context) (AccountSnapshot, bool) {
	for _, account := range m.store.Accounts() {
		if account.Controller {
			continue
		}
		if _, err := os.Stat(filepath.Join(account.CodexHome, "auth.json")); err == nil {
			continue
		}
		snapshot, err := m.accountSnapshot(ctx, account.ID)
		if err != nil || snapshot.Connected {
			continue
		}
		return snapshot, true
	}
	return AccountSnapshot{}, false
}

// RemoveAccount stops a secondary subscription's app-server and forgets it.
func (m *Multiplexer) RemoveAccount(ctx context.Context, id string) error {
	account, ok := m.store.Account(id)
	if !ok {
		return fmt.Errorf("account %q not found", id)
	}
	if account.Controller {
		return errors.New("the primary subscription cannot be removed")
	}
	if m.store.ThreadCounts()[id] > 0 {
		// Checked again under the store lock; this early exit keeps the
		// app-server running when removal is going to be refused anyway.
		_, err := m.store.RemoveAccount(id)
		return err
	}
	m.childrenMu.Lock()
	child := m.children[id]
	delete(m.children, id)
	m.childrenMu.Unlock()
	if child != nil {
		_ = child.Close()
	}
	if _, err := m.store.RemoveAccount(id); err != nil {
		if _, stillKnown := m.store.Account(id); stillKnown {
			// Removal was refused; bring the app-server back.
			_, _ = m.startChild(ctx, account)
		}
		return err
	}
	m.forgetAccountCaches(id)
	m.publish(Event{Type: "account-removed", AccountID: id})
	return nil
}

// RoutingState describes how new chats are assigned.
type RoutingState struct {
	ResetPolicy        string `json:"resetPolicy"`
	Mode               string `json:"mode"`
	PreferredAccountID string `json:"preferredAccountId,omitempty"`
}

func (m *Multiplexer) Routing() RoutingState {
	if preferred := m.store.PreferredAccount(); preferred != "" {
		return RoutingState{ResetPolicy: m.store.ResetPolicy(), Mode: "pinned", PreferredAccountID: preferred}
	}
	return RoutingState{ResetPolicy: m.store.ResetPolicy(), Mode: "automatic"}
}

// SetPreferredAccount pins new chats to one subscription, or restores
// automatic routing when id is empty.
func (m *Multiplexer) SetPreferredAccount(id string) error {
	if err := m.store.SetPreferredAccount(id); err != nil {
		return err
	}
	m.publish(Event{Type: "routing-updated", AccountID: id})
	return nil
}

// RefreshAccount forces a token refresh for one subscription so plan changes
// made on chatgpt.com reach the app-server, then returns a fresh snapshot.
func (m *Multiplexer) RefreshAccount(ctx context.Context, id string) (AccountSnapshot, error) {
	child, ok := m.child(id)
	if !ok {
		return AccountSnapshot{}, fmt.Errorf("account %q is unavailable", id)
	}
	_, refreshErr := child.Request(ctx, "account/read", json.RawMessage(`{"refreshToken":true}`))
	m.markTokenRefreshed(id)
	m.forgetAccountCaches(id)
	snapshot, err := m.accountSnapshot(ctx, id)
	if err != nil {
		return AccountSnapshot{}, err
	}
	if refreshErr != nil && snapshot.UsageError == "" {
		snapshot.UsageError = refreshErr.Error()
	}
	m.publish(Event{Type: "account-updated", AccountID: id, Data: snapshot})
	return snapshot, nil
}

func (m *Multiplexer) forgetAccountCaches(id string) {
	m.profileMu.Lock()
	delete(m.profileCache, id)
	m.profileMu.Unlock()
	m.resetCreditsMu.Lock()
	delete(m.resetCreditsCache, id)
	m.resetCreditsMu.Unlock()
}

func (m *Multiplexer) UpdateAccount(ctx context.Context, id string, label *string, enabled *bool) (AccountSnapshot, error) {
	if _, err := m.store.UpdateAccount(id, label, enabled); err != nil {
		return AccountSnapshot{}, err
	}
	return m.accountSnapshot(ctx, id)
}

func (m *Multiplexer) ThreadAccount(ctx context.Context, threadID string) (AccountSnapshot, error) {
	accountID, ok := m.store.ThreadOwner(threadID)
	if !ok {
		return AccountSnapshot{}, fmt.Errorf("thread %q has no subscription assignment", threadID)
	}
	return m.accountSnapshotWithProfile(ctx, accountID, true)
}

func (m *Multiplexer) StartLogin(ctx context.Context, id, mode string) (json.RawMessage, error) {
	if mode != "chatgpt" && mode != "chatgptDeviceCode" {
		return nil, errors.New("login mode must be chatgpt or chatgptDeviceCode")
	}
	child, ok := m.child(id)
	if !ok {
		return nil, fmt.Errorf("account %q is unavailable", id)
	}
	params, _ := json.Marshal(map[string]any{"type": mode})
	response, err := child.Request(ctx, "account/login/start", params)
	if err != nil {
		return nil, err
	}
	return response.Result, nil
}

func (m *Multiplexer) Logout(ctx context.Context, id string) error {
	child, ok := m.child(id)
	if !ok {
		return fmt.Errorf("account %q is unavailable", id)
	}
	_, err := child.Request(ctx, "account/logout", nil)
	return err
}

func (m *Multiplexer) accountSnapshot(ctx context.Context, accountID string) (AccountSnapshot, error) {
	return m.accountSnapshotWithProfile(ctx, accountID, true)
}

func (m *Multiplexer) accountSnapshotWithProfile(ctx context.Context, accountID string, includeProfile bool) (AccountSnapshot, error) {
	account, ok := m.store.Account(accountID)
	if !ok {
		return AccountSnapshot{}, fmt.Errorf("account %q not found", accountID)
	}
	child, ok := m.child(accountID)
	if !ok {
		return AccountSnapshot{}, fmt.Errorf("account %q app-server is unavailable", accountID)
	}
	params := json.RawMessage(`{"refreshToken":false}`)
	accountResponse, err := child.Request(ctx, "account/read", params)
	if err != nil {
		return AccountSnapshot{}, err
	}
	var accountResult struct {
		Account json.RawMessage `json:"account"`
	}
	if err := json.Unmarshal(accountResponse.Result, &accountResult); err != nil {
		return AccountSnapshot{}, fmt.Errorf("decode account response: %w", err)
	}
	snapshot := AccountSnapshot{
		ID: account.ID, Label: account.Label, Enabled: account.Enabled,
		Controller: account.Controller, Connected: string(accountResult.Account) != "null" && len(accountResult.Account) > 0,
		CreatedAt: account.CreatedAt, RawAccount: accountResult.Account,
		ThreadCount: m.store.ThreadCounts()[account.ID],
		Preferred:   m.store.PreferredAccount() == account.ID,
	}
	if snapshot.Connected {
		var details struct {
			Type     string `json:"type"`
			Email    string `json:"email"`
			PlanType string `json:"planType"`
		}
		_ = json.Unmarshal(accountResult.Account, &details)
		snapshot.AuthType = details.Type
		snapshot.Email = details.Email
		snapshot.PlanType = details.PlanType
		snapshot.PlanLabel = planLabel(details.PlanType)
		if includeProfile {
			snapshot.ProfileImageURL = m.profileImageURLCachedOrSchedule(account)
		}
		if details.Type == "chatgpt" {
			rateResponse, rateErr := child.Request(ctx, "account/rateLimits/read", nil)
			if rateErr == nil {
				var rateResult struct {
					RateLimits RateLimits `json:"rateLimits"`
				}
				if json.Unmarshal(rateResponse.Result, &rateResult) == nil {
					snapshot.RateLimits = &rateResult.RateLimits
					snapshot.Credits = rateResult.RateLimits.Credits
					m.applyLivePlan(&snapshot, details.PlanType)
				}
			} else {
				snapshot.UsageError = rateErr.Error()
				if isAuthRejection(rateErr) {
					snapshot.NeedsReauth = true
					// The refresh token may still be valid even though the
					// access token was revoked; try once before asking the
					// user to sign in again.
					m.scheduleTokenRefresh(account.ID)
				}
			}
		}
	}
	m.applyRateLimitPreview(&snapshot)
	return snapshot, nil
}

// applyLivePlan prefers the plan reported by the usage endpoint. When it
// disagrees with the token's plan the subscription changed on chatgpt.com, so
// the token is refreshed in the background; Codex itself reads the plan from
// the token to decide which models and limits apply.
func (m *Multiplexer) applyLivePlan(snapshot *AccountSnapshot, tokenPlan string) {
	live := ""
	if snapshot.RateLimits != nil {
		live = snapshot.RateLimits.PlanType
	}
	if live == "" || live == "unknown" {
		return
	}
	snapshot.PlanType = live
	snapshot.PlanLabel = planLabel(live)
	if tokenPlan != "" && tokenPlan != live {
		m.scheduleTokenRefresh(snapshot.ID)
	}
}

func isAuthRejection(err error) bool {
	if err == nil {
		return false
	}
	text := strings.ToLower(err.Error())
	for _, marker := range []string{"401", "unauthorized", "token_invalidated", "token_expired", "refresh_token_reused", "invalidated", "sign in again", "signing in again"} {
		if strings.Contains(text, marker) {
			return true
		}
	}
	return false
}

func planLabel(planType string) string {
	switch planType {
	case "free":
		return "Free"
	case "go":
		return "Go"
	case "plus":
		return "Plus"
	case "prolite":
		return "Pro 5x"
	case "pro":
		return "Pro 20x"
	case "team":
		return "Team"
	case "self_serve_business_prolite", "self_serve_business_usage_based", "business":
		return "Business"
	case "ent26", "enterprise_cbp_automation", "enterprise_cbp_usage_based", "enterprise":
		return "Enterprise"
	case "edu":
		return "Edu"
	case "edu_plus":
		return "Edu Plus"
	case "edu_pro":
		return "Edu Pro"
	default:
		return ""
	}
}

func (m *Multiplexer) chooseAccount(ctx context.Context) (state.Account, RouteReason, error) {
	return m.chooseAccountExcluding(ctx, nil)
}

func (m *Multiplexer) chooseAccountExcluding(ctx context.Context, excluded map[string]struct{}) (state.Account, RouteReason, error) {
	snapshots := m.accountSnapshots(ctx, false)
	type candidate struct {
		account      state.Account
		reason       RouteReason
		weekly       *RateLimitWindow
		weeklyUsed   float64
		shortUsed    float64
		resetCredits resetCreditMetadata
		urgency      float64
	}
	candidates := make([]candidate, 0, len(snapshots))
	for _, snapshot := range snapshots {
		if _, skip := excluded[snapshot.ID]; skip {
			continue
		}
		if !snapshot.Enabled || !snapshot.Connected || snapshot.AuthType != "chatgpt" || snapshot.NeedsReauth {
			continue
		}
		account, ok := m.store.Account(snapshot.ID)
		if !ok {
			continue
		}
		weekly, short := longestAndShortestWindow(snapshot.RateLimits)
		if !hasRoutableCapacity(snapshot.RateLimits) {
			continue
		}
		weeklyUsed := 1_000.0
		shortUsed := 1_000.0
		reason := RouteReason{ThreadCount: snapshot.ThreadCount}
		if weekly != nil {
			weeklyUsed = weekly.UsedPercent
			reason.WeeklyUsedPercent = &weekly.UsedPercent
			if weekly.ResetsAt != nil {
				value := *weekly.ResetsAt
				reason.WeeklyResetsAt = &value
			}
		}
		if short != nil {
			shortUsed = short.UsedPercent
			reason.ShortUsedPercent = &short.UsedPercent
		}
		candidates = append(candidates, candidate{
			account: account, reason: reason, weekly: weekly,
			weeklyUsed: weeklyUsed, shortUsed: shortUsed,
		})
	}
	if len(candidates) == 0 {
		return state.Account{}, RouteReason{}, errNoSubscriptionCapacity
	}
	// A pinned subscription wins while it can still take work. Once it is
	// depleted, paused or signed out, routing quietly falls back to automatic
	// so a chat is never refused while another subscription has capacity.
	if preferred := m.store.PreferredAccount(); preferred != "" {
		for _, entry := range candidates {
			if entry.account.ID == preferred {
				entry.reason.Preferred = true
				return entry.account, entry.reason, nil
			}
		}
	}

	type resetResult struct {
		index    int
		metadata resetCreditMetadata
	}
	resetResults := make(chan resetResult, len(candidates))
	for index := range candidates {
		go func(index int, account state.Account) {
			resetResults <- resetResult{
				index: index, metadata: m.routingResetCredits(ctx, account),
			}
		}(index, candidates[index].account)
	}

collectResetCredits:
	for received := 0; received < len(candidates); received++ {
		select {
		case result := <-resetResults:
			candidates[result.index].resetCredits = result.metadata
		case <-ctx.Done():
			break collectResetCredits
		}
	}

	now := m.now()
	for index := range candidates {
		entry := &candidates[index]
		entry.urgency = routeUrgencyScore(now, entry.weekly, entry.resetCredits)
		urgency := entry.urgency
		entry.reason.UrgencyScore = &urgency
		if entry.resetCredits.Known {
			count := entry.resetCredits.AvailableCount
			entry.reason.BankedResetCount = &count
		}
		if entry.resetCredits.EarliestExpiry != nil {
			expiresAt := *entry.resetCredits.EarliestExpiry
			entry.reason.ResetCreditExpiresAt = &expiresAt
		}
	}

	sort.SliceStable(candidates, func(i, j int) bool {
		left, right := candidates[i], candidates[j]
		if math.Abs(left.urgency-right.urgency) > 0.000001 {
			return left.urgency > right.urgency
		}
		if math.Abs(left.shortUsed-right.shortUsed) > 0.001 {
			return left.shortUsed < right.shortUsed
		}
		if math.Abs(left.weeklyUsed-right.weeklyUsed) > 0.001 {
			return left.weeklyUsed < right.weeklyUsed
		}
		if left.reason.ThreadCount != right.reason.ThreadCount {
			return left.reason.ThreadCount < right.reason.ThreadCount
		}
		return left.account.CreatedAt < right.account.CreatedAt
	})
	return candidates[0].account, candidates[0].reason, nil
}

func routeUrgencyScore(now time.Time, weekly *RateLimitWindow, credits resetCreditMetadata) float64 {
	if weekly == nil {
		return -1
	}
	remaining := math.Max(0, math.Min(100, 100-weekly.UsedPercent))
	horizon := routingFallbackWindow
	if weekly.WindowDurationMins != nil && *weekly.WindowDurationMins > 0 {
		horizon = time.Duration(*weekly.WindowDurationMins) * time.Minute
	}
	if weekly.ResetsAt != nil {
		untilReset := time.Unix(*weekly.ResetsAt, 0).Sub(now)
		if untilReset > 0 {
			horizon = untilReset
		}
	}
	if horizon < routingMinimumWindow {
		horizon = routingMinimumWindow
	}
	urgency := remaining / horizon.Hours()
	if credits.Known && credits.AvailableCount > 0 {
		creditCount := min(credits.AvailableCount, routingResetBonusCreditCap)
		urgency *= 1 + float64(creditCount)*routingResetBonusPerCredit
	}
	return urgency
}

func (m *Multiplexer) AggregatedRateLimits(ctx context.Context) (*RateLimits, error) {
	limits, err := aggregateRateLimits(m.accountSnapshots(ctx, false))
	if err != nil {
		return nil, err
	}
	if preview := m.currentRateLimitPreview(); preview != nil && preview.Mode.isAllDepleted() {
		limits.RateLimitReachedType = "legacy_rate_limit_reached"
	}
	return limits, nil
}

func aggregateRateLimits(snapshots []AccountSnapshot) (*RateLimits, error) {
	primary := make([]*RateLimitWindow, 0, len(snapshots))
	secondary := make([]*RateLimitWindow, 0, len(snapshots))
	hasSubscription := false
	hasCapacity := false
	for _, snapshot := range snapshots {
		if !snapshot.Enabled || !snapshot.Connected || snapshot.AuthType != "chatgpt" || snapshot.NeedsReauth {
			continue
		}
		hasSubscription = true
		if snapshot.RateLimits != nil {
			primary = append(primary, snapshot.RateLimits.Primary)
			secondary = append(secondary, snapshot.RateLimits.Secondary)
		}
		if hasRoutableCapacity(snapshot.RateLimits) {
			hasCapacity = true
		}
	}
	if !hasSubscription {
		return nil, errors.New("no enabled ChatGPT subscription is connected")
	}
	result := &RateLimits{
		Primary:   averageRateLimitWindow(primary),
		Secondary: averageRateLimitWindow(secondary),
	}
	if !hasCapacity {
		result.RateLimitReachedType = "rate_limit_reached"
	}
	return result, nil
}

func averageRateLimitWindow(windows []*RateLimitWindow) *RateLimitWindow {
	var used float64
	var count int
	var longestDuration *int64
	var earliestReset *int64
	for _, window := range windows {
		if window == nil {
			continue
		}
		used += window.UsedPercent
		count++
		if window.WindowDurationMins != nil &&
			(longestDuration == nil || *window.WindowDurationMins > *longestDuration) {
			value := *window.WindowDurationMins
			longestDuration = &value
		}
		if window.ResetsAt != nil && (earliestReset == nil || *window.ResetsAt < *earliestReset) {
			value := *window.ResetsAt
			earliestReset = &value
		}
	}
	if count == 0 {
		return nil
	}
	return &RateLimitWindow{
		UsedPercent:        used / float64(count),
		WindowDurationMins: longestDuration,
		ResetsAt:           earliestReset,
	}
}

// hasRoutableCapacity reports whether an account can still accept new work.
//
// ChatGPT enforces a short window (currently five hours) alongside the weekly
// one, and exhausting either stops the account from accepting a request. Only
// the longest window used to be checked, so an account whose short window was
// spent still looked routable while its weekly figure was low, and new threads
// were handed to an account that was already refusing them.
func hasRoutableCapacity(limits *RateLimits) bool {
	longest, shortest := longestAndShortestWindow(limits)
	for _, window := range []*RateLimitWindow{longest, shortest} {
		if window != nil && window.UsedPercent >= 100 {
			return false
		}
	}
	return true
}

func longestAndShortestWindow(limits *RateLimits) (*RateLimitWindow, *RateLimitWindow) {
	if limits == nil {
		return nil, nil
	}
	windows := make([]*RateLimitWindow, 0, 2)
	if limits.Primary != nil {
		windows = append(windows, limits.Primary)
	}
	if limits.Secondary != nil {
		windows = append(windows, limits.Secondary)
	}
	if len(windows) == 0 {
		return nil, nil
	}
	sort.SliceStable(windows, func(i, j int) bool {
		return duration(windows[i]) < duration(windows[j])
	})
	return windows[len(windows)-1], windows[0]
}

func duration(window *RateLimitWindow) int64 {
	if window.WindowDurationMins == nil {
		return 0
	}
	return *window.WindowDurationMins
}

func contextWithControlTimeout(parent context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(parent, 20*time.Second)
}
