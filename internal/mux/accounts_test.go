package mux

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/state"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func TestAccountSnapshotsPreserveUnavailableAccountsAfterDeadline(t *testing.T) {
	multiplexer, accounts, responseDir := newAccountSnapshotTestMultiplexer(t)
	ctx, cancel := context.WithCancel(context.Background())
	snapshotResult := make(chan []AccountSnapshot, 1)
	go func() { snapshotResult <- multiplexer.Accounts(ctx) }()
	waitForAccountSnapshotResponses(t, responseDir, accounts[:2])
	cancel()

	startedAt := time.Now()
	snapshots := <-snapshotResult
	if elapsed := time.Since(startedAt); elapsed > time.Second {
		t.Fatalf("account list did not return promptly after its deadline: %s", elapsed)
	}
	if len(snapshots) != len(accounts) {
		t.Fatalf("account list lost persisted accounts after a child blocked: got %d, want %d (%#v)", len(snapshots), len(accounts), snapshots)
	}
	for index, account := range accounts {
		if snapshots[index].ID != account.ID || snapshots[index].Label != account.Label ||
			snapshots[index].Enabled != account.Enabled || snapshots[index].Controller != account.Controller ||
			snapshots[index].CreatedAt != account.CreatedAt {
			t.Fatalf("persisted account was not preserved at %d: got %#v, want %#v", index, snapshots[index], account)
		}
	}

	for _, responsive := range snapshots[:2] {
		if responsive.Error != "" || !responsive.Connected || responsive.AuthType != "api" {
			t.Fatalf("live child response did not replace its fallback: %#v", responsive)
		}
	}
	blocked := snapshots[len(snapshots)-1]
	if blocked.ID != accounts[len(accounts)-1].ID || blocked.Error != context.Canceled.Error() {
		t.Fatalf("blocked account did not retain an unavailable fallback: %#v", blocked)
	}
	if blocked.Connected {
		t.Fatalf("unavailable account must not be treated as connected or routeable: %#v", blocked)
	}
}

func newAccountSnapshotTestMultiplexer(t *testing.T) (*Multiplexer, []state.Account, string) {
	t.Helper()
	root := t.TempDir()
	primaryHome := filepath.Join(root, "primary")
	if err := os.MkdirAll(primaryHome, 0o700); err != nil {
		t.Fatal(err)
	}
	store, err := state.Open(filepath.Join(root, "mux"), primaryHome)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.AddAccount("Responsive"); err != nil {
		t.Fatal(err)
	}
	blockedAccount, err := store.AddAccount("Blocked")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(blockedAccount.CodexHome, "block-account-read"), []byte("1"), 0o600); err != nil {
		t.Fatal(err)
	}
	responseDir := filepath.Join(root, "account-read-responses")
	if err := os.MkdirAll(responseDir, 0o700); err != nil {
		t.Fatal(err)
	}
	multiplexer, err := New(Options{
		RealExecutable: os.Args[0],
		RealArgs:       []string{"-test.run=TestAccountSnapshotHelperProcess", "--"},
		Environment: append(
			os.Environ(),
			"GO_WANT_ACCOUNT_SNAPSHOT_HELPER=1",
			"CODEX_MUX_TEST_RESPONSE_DIR="+responseDir,
		),
		Store:  store,
		Output: io.Discard,
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, account := range store.Accounts() {
		if _, err := multiplexer.startChild(context.Background(), account); err != nil {
			multiplexer.Close()
			t.Fatal(err)
		}
	}
	t.Cleanup(multiplexer.Close)
	return multiplexer, store.Accounts(), responseDir
}

func waitForAccountSnapshotResponses(t *testing.T, responseDir string, accounts []state.Account) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for {
		ready := true
		for _, account := range accounts {
			if _, err := os.Stat(filepath.Join(responseDir, filepath.Base(account.CodexHome))); err != nil {
				if !os.IsNotExist(err) {
					t.Fatal(err)
				}
				ready = false
			}
		}
		if ready {
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("responsive children did not send account/read responses")
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestAccountSnapshotHelperProcess(t *testing.T) {
	if os.Getenv("GO_WANT_ACCOUNT_SNAPSHOT_HELPER") != "1" {
		return
	}
	_, blocksAccountRead := os.Stat(filepath.Join(os.Getenv("CODEX_HOME"), "block-account-read"))
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		var request struct {
			ID     string `json:"id"`
			Method string `json:"method"`
		}
		if json.Unmarshal(scanner.Bytes(), &request) != nil || request.Method != "account/read" {
			continue
		}
		if blocksAccountRead == nil {
			time.Sleep(time.Hour)
		}
		_, _ = fmt.Fprintf(os.Stdout, `{"id":%q,"result":{"account":{"type":"api"}}}`+"\n", request.ID)
		if responseDir := os.Getenv("CODEX_MUX_TEST_RESPONSE_DIR"); responseDir != "" {
			_ = os.WriteFile(filepath.Join(responseDir, filepath.Base(os.Getenv("CODEX_HOME"))), []byte("1"), 0o600)
		}
	}
	os.Exit(0)
}

func TestPlanLabel(t *testing.T) {
	tests := map[string]string{
		"free":       "Free",
		"go":         "Go",
		"plus":       "Plus",
		"prolite":    "Pro 5x",
		"pro":        "Pro 20x",
		"business":   "Business",
		"enterprise": "Enterprise",
		"edu":        "Edu",
		"unknown":    "",
	}
	for planType, want := range tests {
		if got := planLabel(planType); got != want {
			t.Errorf("planLabel(%q) = %q, want %q", planType, got, want)
		}
	}
}

func TestProfileImageLookupDoesNotBlockAccountSnapshot(t *testing.T) {
	requestStarted := make(chan struct{}, 1)
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		requestStarted <- struct{}{}
		time.Sleep(150 * time.Millisecond)
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     http.Header{"Content-Type": []string{"application/json"}},
			Body:       io.NopCloser(strings.NewReader(`{"profile":{"profile_picture_url":"https://example.com/avatar.png"}}`)),
			Request:    request,
		}, nil
	})}

	codexHome := t.TempDir()
	auth, err := json.Marshal(map[string]any{
		"tokens": map[string]string{"access_token": "test-token", "account_id": "test-account"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(codexHome, "auth.json"), auth, 0o600); err != nil {
		t.Fatal(err)
	}
	multiplexer := &Multiplexer{
		profileClient:   client,
		profileEndpoint: "https://chatgpt.test/profile",
		profileCache:    make(map[string]profileCacheEntry),
		profilePending:  make(map[string]bool),
		now:             time.Now,
	}
	account := state.Account{ID: "primary", CodexHome: codexHome}

	startedAt := time.Now()
	if imageURL := multiplexer.profileImageURLCachedOrSchedule(account); imageURL != "" {
		t.Fatalf("first uncached lookup should return immediately, got %q", imageURL)
	}
	if elapsed := time.Since(startedAt); elapsed > 50*time.Millisecond {
		t.Fatalf("uncached profile lookup blocked the account snapshot for %s", elapsed)
	}
	select {
	case <-requestStarted:
	case <-time.After(time.Second):
		t.Fatal("background profile request did not start")
	}

	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if imageURL := multiplexer.profileImageURLCachedOrSchedule(account); imageURL != "" {
			if imageURL != "https://example.com/avatar.png" {
				t.Fatalf("unexpected cached image URL %q", imageURL)
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("background profile lookup did not populate the cache")
}

func TestLongestAndShortestWindowUsesQuotaDuration(t *testing.T) {
	shortMinutes := int64(300)
	weeklyMinutes := int64(10_080)
	short := &RateLimitWindow{UsedPercent: 72, WindowDurationMins: &shortMinutes}
	weekly := &RateLimitWindow{UsedPercent: 31, WindowDurationMins: &weeklyMinutes}

	longest, shortest := longestAndShortestWindow(&RateLimits{
		Primary: short, Secondary: weekly,
	})
	if longest != weekly || shortest != short {
		t.Fatalf("windows were not ordered by duration: longest=%#v shortest=%#v", longest, shortest)
	}
}

func TestLongestAndShortestWindowHandlesSingleWindow(t *testing.T) {
	minutes := int64(300)
	only := &RateLimitWindow{UsedPercent: 12, WindowDurationMins: &minutes}
	longest, shortest := longestAndShortestWindow(&RateLimits{Primary: only})
	if longest != only || shortest != only {
		t.Fatalf("single window should serve both roles: longest=%#v shortest=%#v", longest, shortest)
	}
}

func TestAggregateRateLimitsKeepsPoolAvailable(t *testing.T) {
	weeklyMinutes := int64(10_080)
	limits, err := aggregateRateLimits([]AccountSnapshot{
		{
			ID: "one", Enabled: true, Connected: true, AuthType: "chatgpt",
			RateLimits: &RateLimits{Primary: &RateLimitWindow{
				UsedPercent: 100, WindowDurationMins: &weeklyMinutes,
			}},
		},
		{
			ID: "two", Enabled: true, Connected: true, AuthType: "chatgpt",
			RateLimits: &RateLimits{Primary: &RateLimitWindow{
				UsedPercent: 20, WindowDurationMins: &weeklyMinutes,
			}},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if limits.Primary == nil || limits.Primary.UsedPercent != 60 {
		t.Fatalf("expected pooled usage to average to 60%%, got %#v", limits.Primary)
	}
	if limits.RateLimitReachedType != nil {
		t.Fatalf("pool should remain available while one account has capacity: %#v", limits)
	}
}

func TestAggregateRateLimitsReportsAllDepleted(t *testing.T) {
	limits, err := aggregateRateLimits([]AccountSnapshot{
		{
			ID: "one", Enabled: true, Connected: true, AuthType: "chatgpt",
			RateLimits: &RateLimits{Primary: &RateLimitWindow{UsedPercent: 100}},
		},
		{
			ID: "two", Enabled: true, Connected: true, AuthType: "chatgpt",
			RateLimits: &RateLimits{Primary: &RateLimitWindow{UsedPercent: 100}},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if limits.RateLimitReachedType != "rate_limit_reached" {
		t.Fatalf("expected the pool to report depletion, got %#v", limits)
	}
}

func TestRouteUrgencyPrefersQuotaExpiringSooner(t *testing.T) {
	now := time.Date(2026, time.August, 16, 12, 0, 0, 0, time.UTC)
	weeklyMinutes := int64(10_080)
	soon := now.Add(24 * time.Hour).Unix()
	later := now.Add(6 * 24 * time.Hour).Unix()
	soonScore := routeUrgencyScore(now, &RateLimitWindow{
		UsedPercent: 40, WindowDurationMins: &weeklyMinutes, ResetsAt: &soon,
	}, resetCreditMetadata{})
	laterScore := routeUrgencyScore(now, &RateLimitWindow{
		UsedPercent: 40, WindowDurationMins: &weeklyMinutes, ResetsAt: &later,
	}, resetCreditMetadata{})
	if soonScore <= laterScore {
		t.Fatalf("sooner reset should be more urgent: soon=%f later=%f", soonScore, laterScore)
	}
}

func TestRouteUrgencyWeightsBankedResetsWithoutDominating(t *testing.T) {
	now := time.Date(2026, time.August, 16, 12, 0, 0, 0, time.UTC)
	weeklyMinutes := int64(10_080)
	reset := now.Add(4 * 24 * time.Hour).Unix()
	window := &RateLimitWindow{
		UsedPercent: 50, WindowDurationMins: &weeklyMinutes, ResetsAt: &reset,
	}
	plain := routeUrgencyScore(now, window, resetCreditMetadata{Known: true})
	banked := routeUrgencyScore(now, window, resetCreditMetadata{Known: true, AvailableCount: 2})
	if banked <= plain {
		t.Fatalf("banked resets should increase urgency: plain=%f banked=%f", plain, banked)
	}
	if banked > plain*1.31 {
		t.Fatalf("banked reset bonus should remain bounded: plain=%f banked=%f", plain, banked)
	}
}

func TestRouteUrgencyCapsResetBonus(t *testing.T) {
	now := time.Date(2026, time.August, 16, 12, 0, 0, 0, time.UTC)
	reset := now.Add(7 * 24 * time.Hour).Unix()
	window := &RateLimitWindow{UsedPercent: 20, ResetsAt: &reset}
	three := routeUrgencyScore(now, window, resetCreditMetadata{Known: true, AvailableCount: 3})
	ten := routeUrgencyScore(now, window, resetCreditMetadata{Known: true, AvailableCount: 10})
	if three != ten {
		t.Fatalf("reset bonus cap was not applied: three=%f ten=%f", three, ten)
	}
}

func TestRouteUrgencyFallsBackToWeeklyUtilization(t *testing.T) {
	now := time.Date(2026, time.August, 16, 12, 0, 0, 0, time.UTC)
	weeklyMinutes := int64(10_080)
	lessUsed := routeUrgencyScore(now, &RateLimitWindow{
		UsedPercent: 20, WindowDurationMins: &weeklyMinutes,
	}, resetCreditMetadata{})
	moreUsed := routeUrgencyScore(now, &RateLimitWindow{
		UsedPercent: 80, WindowDurationMins: &weeklyMinutes,
	}, resetCreditMetadata{})
	if lessUsed <= moreUsed {
		t.Fatalf("fallback should prefer the less-used account: less=%f more=%f", lessUsed, moreUsed)
	}
}
