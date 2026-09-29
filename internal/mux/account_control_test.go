package mux

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/state"
)

// TestAccountControlHelperProcess is a scripted app-server. Each account's
// CODEX_HOME holds the replies: account.json for account/read,
// ratelimits.json or ratelimits-error for account/rateLimits/read. Token
// refreshes are recorded in refresh-requested.
func TestAccountControlHelperProcess(t *testing.T) {
	if os.Getenv("GO_WANT_ACCOUNT_CONTROL_HELPER") != "1" {
		return
	}
	home := os.Getenv("CODEX_HOME")
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		var request struct {
			ID     string          `json:"id"`
			Method string          `json:"method"`
			Params json.RawMessage `json:"params"`
		}
		if json.Unmarshal(scanner.Bytes(), &request) != nil || request.ID == "" {
			continue
		}
		switch request.Method {
		case "account/read":
			var params struct {
				RefreshToken bool `json:"refreshToken"`
			}
			_ = json.Unmarshal(request.Params, &params)
			if params.RefreshToken {
				_ = os.WriteFile(filepath.Join(home, "refresh-requested"), []byte("1"), 0o600)
			}
			account, err := os.ReadFile(filepath.Join(home, "account.json"))
			if err != nil {
				account = []byte("null")
			}
			fmt.Fprintf(os.Stdout, `{"id":%q,"result":{"account":%s,"requiresOpenaiAuth":true}}`+"\n", request.ID, account)
		case "account/rateLimits/read":
			if message, err := os.ReadFile(filepath.Join(home, "ratelimits-error")); err == nil {
				encoded, _ := json.Marshal(string(message))
				fmt.Fprintf(os.Stdout, `{"id":%q,"error":{"code":-32603,"message":%s}}`+"\n", request.ID, encoded)
				continue
			}
			limits, err := os.ReadFile(filepath.Join(home, "ratelimits.json"))
			if err != nil {
				limits = []byte(`{}`)
			}
			fmt.Fprintf(os.Stdout, `{"id":%q,"result":{"rateLimits":%s}}`+"\n", request.ID, limits)
		default:
			fmt.Fprintf(os.Stdout, `{"id":%q,"result":{}}`+"\n", request.ID)
		}
	}
	os.Exit(0)
}

type controlTestAccount struct {
	label      string
	account    string
	rateLimits string
	rateError  string
}

func newAccountControlMultiplexer(t *testing.T, specs ...controlTestAccount) (*Multiplexer, *state.Store, []state.Account) {
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
	accounts := store.Accounts()
	for index, spec := range specs {
		account := accounts[0]
		if index > 0 {
			account, err = store.AddAccount(spec.label)
			if err != nil {
				t.Fatal(err)
			}
			accounts = append(accounts, account)
		}
		write := func(name, contents string) {
			if contents == "" {
				return
			}
			if err := os.WriteFile(filepath.Join(account.CodexHome, name), []byte(contents), 0o600); err != nil {
				t.Fatal(err)
			}
		}
		write("account.json", spec.account)
		write("ratelimits.json", spec.rateLimits)
		write("ratelimits-error", spec.rateError)
	}
	multiplexer, err := New(Options{
		RealExecutable: os.Args[0],
		RealArgs:       []string{"-test.run=TestAccountControlHelperProcess", "--"},
		Environment:    append(os.Environ(), "GO_WANT_ACCOUNT_CONTROL_HELPER=1"),
		Store:          store,
		Output:         io.Discard,
	})
	if err != nil {
		t.Fatal(err)
	}
	// Keep reset-credit lookups off the network.
	multiplexer.resetCreditsEndpoint = "http://127.0.0.1:1/unused"
	for _, account := range store.Accounts() {
		if _, err := multiplexer.startChild(context.Background(), account); err != nil {
			multiplexer.Close()
			t.Fatal(err)
		}
	}
	t.Cleanup(multiplexer.Close)
	return multiplexer, store, store.Accounts()
}

const (
	plusAccount     = `{"type":"chatgpt","email":"a@example.com","planType":"plus"}`
	freeAccount     = `{"type":"chatgpt","email":"b@example.com","planType":"free"}`
	healthyLimits   = `{"primary":{"usedPercent":10,"windowDurationMins":300,"resetsAt":4102444800},"secondary":{"usedPercent":20,"windowDurationMins":10080,"resetsAt":4102444800},"planType":"plus","credits":{"hasCredits":true,"unlimited":false,"balance":"42.5"}}`
	urgentLimits    = `{"primary":{"usedPercent":0,"windowDurationMins":300,"resetsAt":4102444800},"secondary":{"usedPercent":0,"windowDurationMins":10080,"resetsAt":1},"planType":"plus"}`
	depletedLimits  = `{"primary":{"usedPercent":100,"windowDurationMins":300,"resetsAt":4102444800},"secondary":{"usedPercent":50,"windowDurationMins":10080,"resetsAt":4102444800},"planType":"plus"}`
	tokenRejection  = `failed to fetch codex rate limits: GET https://chatgpt.com/backend-api/wham/usage failed: 401 Unauthorized; body={"error":{"code":"token_invalidated"}}`
	upgradedLimits  = `{"primary":{"usedPercent":0,"windowDurationMins":300},"secondary":{"usedPercent":0,"windowDurationMins":10080},"planType":"pro"}`
	transientFailed = `failed to fetch codex rate limits: connection reset by peer`
)

func TestSnapshotUsesLivePlanAndRefreshesStaleToken(t *testing.T) {
	multiplexer, _, accounts := newAccountControlMultiplexer(t,
		controlTestAccount{account: freeAccount, rateLimits: upgradedLimits},
	)
	snapshot, err := multiplexer.accountSnapshot(context.Background(), accounts[0].ID)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.PlanType != "pro" || snapshot.PlanLabel != "Pro 20x" {
		t.Fatalf("snapshot kept the token's stale plan: %q / %q", snapshot.PlanType, snapshot.PlanLabel)
	}
	waitForFile(t, filepath.Join(accounts[0].CodexHome, "refresh-requested"))
}

func TestSnapshotExposesCredits(t *testing.T) {
	multiplexer, _, accounts := newAccountControlMultiplexer(t,
		controlTestAccount{account: plusAccount, rateLimits: healthyLimits},
	)
	snapshot, err := multiplexer.accountSnapshot(context.Background(), accounts[0].ID)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Credits == nil || !snapshot.Credits.HasCredits || snapshot.Credits.Balance == nil || *snapshot.Credits.Balance != "42.5" {
		t.Fatalf("credits were not exposed: %#v", snapshot.Credits)
	}
	if _, err := os.Stat(filepath.Join(accounts[0].CodexHome, "refresh-requested")); err == nil {
		t.Fatal("a matching plan must not trigger a token refresh")
	}
}

func TestRejectedTokenIsFlaggedAndNeverRouted(t *testing.T) {
	multiplexer, _, accounts := newAccountControlMultiplexer(t,
		controlTestAccount{account: plusAccount, rateLimits: depletedLimits},
		controlTestAccount{label: "Upgraded", account: freeAccount, rateError: tokenRejection},
	)
	snapshot, err := multiplexer.accountSnapshot(context.Background(), accounts[1].ID)
	if err != nil {
		t.Fatal(err)
	}
	if !snapshot.NeedsReauth || snapshot.UsageError == "" {
		t.Fatalf("rejected sign-in was not flagged: %#v", snapshot)
	}
	if _, _, err := multiplexer.chooseAccount(context.Background()); err != errNoSubscriptionCapacity {
		t.Fatalf("an account with a rejected token must not receive chats, got %v", err)
	}
	waitForFile(t, filepath.Join(accounts[1].CodexHome, "refresh-requested"))
}

func TestTransientUsageFailureIsNotTreatedAsSignOut(t *testing.T) {
	multiplexer, _, accounts := newAccountControlMultiplexer(t,
		controlTestAccount{account: plusAccount, rateError: transientFailed},
	)
	snapshot, err := multiplexer.accountSnapshot(context.Background(), accounts[0].ID)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.NeedsReauth {
		t.Fatalf("a network failure was reported as a sign-out: %#v", snapshot)
	}
}

func TestPreferredAccountWinsWhileItHasCapacity(t *testing.T) {
	multiplexer, store, accounts := newAccountControlMultiplexer(t,
		controlTestAccount{account: plusAccount, rateLimits: urgentLimits},
		controlTestAccount{label: "Pinned", account: plusAccount, rateLimits: healthyLimits},
	)
	chosen, _, err := multiplexer.chooseAccount(context.Background())
	if err != nil || chosen.ID != accounts[0].ID {
		t.Fatalf("automatic routing should pick the urgent account, got %q (%v)", chosen.ID, err)
	}
	if err := multiplexer.SetPreferredAccount(accounts[1].ID); err != nil {
		t.Fatal(err)
	}
	chosen, reason, err := multiplexer.chooseAccount(context.Background())
	if err != nil || chosen.ID != accounts[1].ID || !reason.Preferred {
		t.Fatalf("pinned account was not used: %q preferred=%v (%v)", chosen.ID, reason.Preferred, err)
	}
	if routing := multiplexer.Routing(); routing.Mode != "pinned" || routing.PreferredAccountID != accounts[1].ID {
		t.Fatalf("unexpected routing state: %#v", routing)
	}

	paused := false
	if _, err := store.UpdateAccount(accounts[1].ID, nil, &paused); err != nil {
		t.Fatal(err)
	}
	chosen, _, err = multiplexer.chooseAccount(context.Background())
	if err != nil || chosen.ID != accounts[0].ID {
		t.Fatalf("a paused pinned account must fall back to automatic routing, got %q (%v)", chosen.ID, err)
	}
}

func TestDepletedPreferredAccountFallsBack(t *testing.T) {
	multiplexer, _, accounts := newAccountControlMultiplexer(t,
		controlTestAccount{account: plusAccount, rateLimits: healthyLimits},
		controlTestAccount{label: "Pinned", account: plusAccount, rateLimits: depletedLimits},
	)
	if err := multiplexer.SetPreferredAccount(accounts[1].ID); err != nil {
		t.Fatal(err)
	}
	chosen, reason, err := multiplexer.chooseAccount(context.Background())
	if err != nil || chosen.ID != accounts[0].ID || reason.Preferred {
		t.Fatalf("a depleted pin must fall back, got %q preferred=%v (%v)", chosen.ID, reason.Preferred, err)
	}
}

func TestAddAccountReusesUnfinishedSignIn(t *testing.T) {
	multiplexer, store, accounts := newAccountControlMultiplexer(t,
		controlTestAccount{account: plusAccount, rateLimits: healthyLimits},
		controlTestAccount{label: "Abandoned"},
	)
	added, err := multiplexer.AddAccount(context.Background(), "")
	if err != nil {
		t.Fatal(err)
	}
	if added.ID != accounts[1].ID {
		t.Fatalf("an abandoned sign-in slot was not reused: got %q, want %q", added.ID, accounts[1].ID)
	}
	if count := len(store.Accounts()); count != 2 {
		t.Fatalf("adding a subscription created another slot: %d accounts", count)
	}
}

func TestRemoveAccountStopsChildAndRetiresHome(t *testing.T) {
	multiplexer, store, accounts := newAccountControlMultiplexer(t,
		controlTestAccount{account: plusAccount, rateLimits: healthyLimits},
		controlTestAccount{label: "Busy", account: plusAccount, rateLimits: healthyLimits},
		controlTestAccount{label: "Spare"},
	)
	if err := multiplexer.RemoveAccount(context.Background(), accounts[0].ID); err == nil {
		t.Fatal("the primary subscription must not be removable")
	}
	if err := store.SetThreadOwner("thread-1", accounts[1].ID); err != nil {
		t.Fatal(err)
	}
	if err := multiplexer.RemoveAccount(context.Background(), accounts[1].ID); err == nil {
		t.Fatal("an account that owns chats must not be removable")
	}
	if _, ok := multiplexer.child(accounts[1].ID); !ok {
		t.Fatal("a refused removal stopped the account's app-server")
	}
	if err := multiplexer.SetPreferredAccount(accounts[2].ID); err != nil {
		t.Fatal(err)
	}
	if err := multiplexer.RemoveAccount(context.Background(), accounts[2].ID); err != nil {
		t.Fatal(err)
	}
	if _, ok := store.Account(accounts[2].ID); ok {
		t.Fatal("removed account is still persisted")
	}
	if _, ok := multiplexer.child(accounts[2].ID); ok {
		t.Fatal("removed account's app-server is still registered")
	}
	if multiplexer.Routing().Mode != "automatic" {
		t.Fatal("removing the pinned account must restore automatic routing")
	}
	if _, err := os.Stat(filepath.Dir(accounts[2].CodexHome)); !os.IsNotExist(err) {
		t.Fatalf("removed account home was not retired: %v", err)
	}
	retired, _ := filepath.Glob(filepath.Join(store.Root(), "removed", accounts[2].ID+"-*"))
	if len(retired) != 1 {
		t.Fatalf("removed account home was not kept for recovery: %v", retired)
	}
}

func TestMergeThreadListsKeepsOwnerAndDeduplicates(t *testing.T) {
	owners := map[string]string{"moved": "b"}
	adopted := map[string]string{}
	results := []accountThreads{
		{accountID: "a", threads: []map[string]any{
			{"id": "moved", "preview": "stale copy"},
			{"id": "only-a"},
		}},
		{accountID: "b", threads: []map[string]any{
			{"id": "moved", "preview": "current copy"},
			{"id": "new-shared"},
		}},
		{accountID: "c", threads: []map[string]any{{"id": "new-shared"}}},
	}
	threads := mergeThreadLists(results, func(id string) (string, bool) {
		owner, ok := owners[id]
		return owner, ok
	}, func(id, account string) { adopted[id] = account })

	if len(threads) != 3 {
		t.Fatalf("threads were not deduplicated: %#v", threads)
	}
	for _, thread := range threads {
		if thread["id"] == "moved" && thread["preview"] != "current copy" {
			t.Fatalf("the owner's copy of a moved chat was not kept: %#v", thread)
		}
	}
	if _, reassigned := adopted["moved"]; reassigned {
		t.Fatal("a known owner was replaced by a stale listing")
	}
	if adopted["only-a"] != "a" || adopted["new-shared"] != "b" {
		t.Fatalf("unknown threads were not adopted deterministically: %#v", adopted)
	}
}

func TestIsAuthRejection(t *testing.T) {
	for _, text := range []string{tokenRejection, "account/rateLimits/read: 401 Unauthorized", "Your authentication token has been invalidated. Please try signing in again."} {
		if !isAuthRejection(fmt.Errorf("%s", text)) {
			t.Fatalf("not recognized as a sign-in rejection: %q", text)
		}
	}
	for _, text := range []string{transientFailed, "context deadline exceeded"} {
		if isAuthRejection(fmt.Errorf("%s", text)) {
			t.Fatalf("misclassified as a sign-in rejection: %q", text)
		}
	}
}

func waitForFile(t *testing.T, path string) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if _, err := os.Stat(path); err == nil {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", path)
}
