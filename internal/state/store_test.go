package state

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStoreBootstrapsPrimaryAndPersistsThreadAffinity(t *testing.T) {
	root := t.TempDir()
	primaryHome := filepath.Join(root, "primary")
	store, err := Open(filepath.Join(root, "mux"), primaryHome)
	if err != nil {
		t.Fatal(err)
	}
	accounts := store.Accounts()
	if len(accounts) != 1 || accounts[0].ID != "primary" || !accounts[0].Controller {
		t.Fatalf("unexpected bootstrap accounts: %#v", accounts)
	}
	added, err := store.AddAccount("Work")
	if err != nil {
		t.Fatal(err)
	}
	config, err := os.ReadFile(filepath.Join(added.CodexHome, "config.toml"))
	if err != nil {
		t.Fatal(err)
	}
	wantConfig := "cli_auth_credentials_store = \"file\"\nmcp_oauth_credentials_store = \"file\"\n"
	if string(config) != wantConfig {
		t.Fatalf("unexpected isolated config: %q", config)
	}
	if err := store.SetThreadOwner("thread-1", added.ID); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(filepath.Join(root, "mux"), primaryHome)
	if err != nil {
		t.Fatal(err)
	}
	owner, ok := reopened.ThreadOwner("thread-1")
	if !ok || owner != added.ID {
		t.Fatalf("thread affinity was not persisted: owner=%q ok=%v", owner, ok)
	}
}

func TestAccountConfigInheritsManagedMCPAndPreservesLocalProjects(t *testing.T) {
	root := t.TempDir()
	primaryHome := filepath.Join(root, "primary")
	if err := os.MkdirAll(primaryHome, 0o700); err != nil {
		t.Fatal(err)
	}
	primaryConfig := `model = "gpt-test"

[mcp_servers.node_repl]
command = "/Applications/Codex Subscription Router.app/node_repl"

[mcp_servers.node_repl.env]
SKY_CUA_SERVICE_PATH = "/Applications/Codex Subscription Router Computer Use.app"

[projects."/primary-only"]
trust_level = "trusted"
`
	if err := os.WriteFile(filepath.Join(primaryHome, "config.toml"), []byte(primaryConfig), 0o600); err != nil {
		t.Fatal(err)
	}

	muxRoot := filepath.Join(root, "mux")
	store, err := Open(muxRoot, primaryHome)
	if err != nil {
		t.Fatal(err)
	}
	added, err := store.AddAccount("Work")
	if err != nil {
		t.Fatal(err)
	}
	configPath := filepath.Join(added.CodexHome, "config.toml")
	config, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatal(err)
	}
	text := string(config)
	for _, expected := range []string{
		`cli_auth_credentials_store = "file"`,
		`mcp_oauth_credentials_store = "file"`,
		`model = "gpt-test"`,
		`[mcp_servers.node_repl]`,
		`SKY_CUA_SERVICE_PATH = "/Applications/Codex Subscription Router Computer Use.app"`,
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("account config is missing %q:\n%s", expected, text)
		}
	}
	// Every subscription works on the same local folders, so Primary's
	// project trust is shared rather than re-prompted per account.
	if !strings.Contains(text, "/primary-only") {
		t.Fatalf("primary project trust was not shared with the account:\n%s", text)
	}

	text += `
[projects."/account-project"]
trust_level = "trusted"
`
	if err := os.WriteFile(configPath, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	primaryConfig = strings.ReplaceAll(primaryConfig, "gpt-test", "gpt-updated")
	if err := os.WriteFile(filepath.Join(primaryHome, "config.toml"), []byte(primaryConfig), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(muxRoot, primaryHome); err != nil {
		t.Fatal(err)
	}
	config, err = os.ReadFile(configPath)
	if err != nil {
		t.Fatal(err)
	}
	text = string(config)
	if !strings.Contains(text, `model = "gpt-updated"`) {
		t.Fatalf("managed config was not refreshed:\n%s", text)
	}
	if !strings.Contains(text, `[projects."/account-project"]`) {
		t.Fatalf("account project trust was not preserved:\n%s", text)
	}
}

func TestSyncManagedConfigPropagatesPluginsWithoutRestart(t *testing.T) {
	root := t.TempDir()
	primaryHome := filepath.Join(root, "primary")
	if err := os.MkdirAll(primaryHome, 0o700); err != nil {
		t.Fatal(err)
	}
	configPath := filepath.Join(primaryHome, "config.toml")
	if err := os.WriteFile(configPath, []byte("model = \"before\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := Open(filepath.Join(root, "mux"), primaryHome)
	if err != nil {
		t.Fatal(err)
	}
	account, err := store.AddAccount("Work")
	if err != nil {
		t.Fatal(err)
	}
	updated := "model = \"after\"\n\n[plugins.\"browser@openai-bundled\"]\nenabled = true\n"
	if err := os.WriteFile(configPath, []byte(updated), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := store.SyncManagedConfig(); err != nil {
		t.Fatal(err)
	}
	isolated, err := os.ReadFile(filepath.Join(account.CodexHome, "config.toml"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(isolated), `[plugins."browser@openai-bundled"]`) {
		t.Fatalf("plugin config did not propagate:\n%s", isolated)
	}
}

func TestUpdateAccountPreservesController(t *testing.T) {
	root := t.TempDir()
	store, err := Open(root, filepath.Join(root, "primary"))
	if err != nil {
		t.Fatal(err)
	}
	label := "Personal"
	enabled := false
	account, err := store.UpdateAccount("primary", &label, &enabled)
	if err != nil {
		t.Fatal(err)
	}
	if account.Label != label || account.Enabled || !account.Controller {
		t.Fatalf("unexpected updated account: %#v", account)
	}
}

func TestDefaultLabelsSkipTakenNumbers(t *testing.T) {
	root := t.TempDir()
	store, err := Open(filepath.Join(root, "mux"), filepath.Join(root, "primary"))
	if err != nil {
		t.Fatal(err)
	}
	second, _ := store.AddAccount("")
	third, _ := store.AddAccount("")
	if second.Label != "Subscription 2" || third.Label != "Subscription 3" {
		t.Fatalf("unexpected default labels: %q, %q", second.Label, third.Label)
	}
	if _, err := store.RemoveAccount(second.ID); err != nil {
		t.Fatal(err)
	}
	fourth, _ := store.AddAccount("")
	if fourth.Label != "Subscription 2" {
		t.Fatalf("default label reused a taken number: %q", fourth.Label)
	}
	fifth, _ := store.AddAccount("")
	if fifth.Label != "Subscription 4" {
		t.Fatalf("default label duplicated an existing one: %q", fifth.Label)
	}
}

func TestPreferredAccountPersists(t *testing.T) {
	root := t.TempDir()
	primaryHome := filepath.Join(root, "primary")
	store, err := Open(filepath.Join(root, "mux"), primaryHome)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SetPreferredAccount("missing"); err == nil {
		t.Fatal("pinning an unknown account must fail")
	}
	added, _ := store.AddAccount("Work")
	if err := store.SetPreferredAccount(added.ID); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(filepath.Join(root, "mux"), primaryHome)
	if err != nil {
		t.Fatal(err)
	}
	if reopened.PreferredAccount() != added.ID {
		t.Fatalf("preferred account was not persisted: %q", reopened.PreferredAccount())
	}
	if err := reopened.SetPreferredAccount(""); err != nil || reopened.PreferredAccount() != "" {
		t.Fatalf("automatic routing was not restored: %q (%v)", reopened.PreferredAccount(), err)
	}
}

func TestRemoveAccountProtectsPrimaryAndOwnedChats(t *testing.T) {
	root := t.TempDir()
	store, err := Open(filepath.Join(root, "mux"), filepath.Join(root, "primary"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.RemoveAccount("primary"); err == nil {
		t.Fatal("primary account was removable")
	}
	added, _ := store.AddAccount("Work")
	if err := store.SetThreadOwner("thread-1", added.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := store.RemoveAccount(added.ID); err == nil || !strings.Contains(err.Error(), "owns 1 chat") {
		t.Fatalf("an account owning chats was removable: %v", err)
	}
}

func TestIsolatedAccountsInheritPrimaryProjectTrust(t *testing.T) {
	root := t.TempDir()
	primaryHome := filepath.Join(root, "primary")
	if err := os.MkdirAll(primaryHome, 0o700); err != nil {
		t.Fatal(err)
	}
	primaryConfig := `model = "gpt-test"

[projects."/work/app"]
trust_level = "trusted"

[projects."/work/shared"]
trust_level = "trusted"
`
	if err := os.WriteFile(filepath.Join(primaryHome, "config.toml"), []byte(primaryConfig), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := Open(filepath.Join(root, "mux"), primaryHome)
	if err != nil {
		t.Fatal(err)
	}
	added, err := store.AddAccount("Work")
	if err != nil {
		t.Fatal(err)
	}
	isolatedPath := filepath.Join(added.CodexHome, "config.toml")
	existing, _ := os.ReadFile(isolatedPath)
	local := string(existing) + `
[projects."/work/only-here"]
trust_level = "trusted"

[projects."/work/shared"]
trust_level = "untrusted"
`
	if err := os.WriteFile(isolatedPath, []byte(local), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := store.SyncManagedConfig(); err != nil {
		t.Fatal(err)
	}
	synced, _ := os.ReadFile(isolatedPath)
	text := string(synced)
	for _, want := range []string{`[projects."/work/app"]`, `[projects."/work/only-here"]`} {
		if !strings.Contains(text, want) {
			t.Fatalf("missing %s in synced config:\n%s", want, text)
		}
	}
	if strings.Count(text, `[projects."/work/shared"]`) != 1 || strings.Contains(text, "untrusted") {
		t.Fatalf("primary project trust did not win:\n%s", text)
	}
	if err := store.SyncManagedConfig(); err != nil {
		t.Fatal(err)
	}
	again, _ := os.ReadFile(isolatedPath)
	if string(again) != text {
		t.Fatalf("config sync is not stable:\n%s\n---\n%s", text, again)
	}
}

func TestResetPolicyDefaultsAndPersists(t *testing.T) {
	root, home := t.TempDir(), t.TempDir()
	s, err := Open(root, home)
	if err != nil {
		t.Fatal(err)
	}
	if s.ResetPolicy() != "ask" {
		t.Fatal("reset policy must default to ask")
	}
	if err := s.SetResetPolicy("auto"); err != nil {
		t.Fatal(err)
	}
	loaded, err := Open(root, home)
	if err != nil || loaded.ResetPolicy() != "auto" {
		t.Fatalf("policy not persisted: %v", err)
	}
	if err := loaded.SetResetPolicy("unknown"); err == nil || loaded.ResetPolicy() != "auto" {
		t.Fatal("invalid policy changed setting")
	}
	if err := loaded.SetResetPolicy("ask"); err != nil {
		t.Fatal(err)
	}
	loaded, _ = Open(root, home)
	if loaded.ResetPolicy() != "ask" {
		t.Fatal("ask mode not persisted")
	}
}
