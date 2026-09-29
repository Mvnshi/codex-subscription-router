package state

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"
)

const stateVersion = 1

type Account struct {
	ID         string `json:"id"`
	Label      string `json:"label"`
	CodexHome  string `json:"codexHome"`
	Enabled    bool   `json:"enabled"`
	Controller bool   `json:"controller"`
	CreatedAt  int64  `json:"createdAt"`
}

type persistedState struct {
	Version     int               `json:"version"`
	Accounts    []Account         `json:"accounts"`
	ThreadOwner map[string]string `json:"threadOwner"`
	// PreferredAccount pins new chats to one subscription while it has
	// capacity. Empty means automatic routing. Older builds ignore the field.
	PreferredAccount string `json:"preferredAccount,omitempty"`
}

// Store persists only routing metadata. OAuth credentials and conversation
// databases remain inside each account's isolated Codex home.
type Store struct {
	mu               sync.RWMutex
	root             string
	path             string
	primaryCodexHome string
	accounts         []Account
	owners           map[string]string
	preferred        string
}

func Open(root, primaryCodexHome string) (*Store, error) {
	if root == "" {
		return nil, errors.New("state root is required")
	}
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, fmt.Errorf("create state root: %w", err)
	}
	if err := os.Chmod(root, 0o700); err != nil {
		return nil, fmt.Errorf("secure state root: %w", err)
	}

	store := &Store{
		root:             root,
		path:             filepath.Join(root, "state.json"),
		primaryCodexHome: primaryCodexHome,
		owners:           make(map[string]string),
	}
	data, err := os.ReadFile(store.path)
	switch {
	case err == nil:
		var persisted persistedState
		if err := json.Unmarshal(data, &persisted); err != nil {
			return nil, fmt.Errorf("read state: %w", err)
		}
		if persisted.Version != stateVersion {
			return nil, fmt.Errorf("unsupported state version %d", persisted.Version)
		}
		store.accounts = persisted.Accounts
		if persisted.ThreadOwner != nil {
			store.owners = persisted.ThreadOwner
		}
		store.preferred = persisted.PreferredAccount
	case errors.Is(err, os.ErrNotExist):
		store.accounts = []Account{{
			ID:         "primary",
			Label:      "Primary",
			CodexHome:  primaryCodexHome,
			Enabled:    true,
			Controller: true,
			CreatedAt:  time.Now().Unix(),
		}}
		if err := store.saveLocked(); err != nil {
			return nil, err
		}
	default:
		return nil, fmt.Errorf("read state: %w", err)
	}
	for _, account := range store.accounts {
		if samePath(account.CodexHome, primaryCodexHome) {
			continue
		}
		if err := syncIsolatedConfig(primaryCodexHome, account.CodexHome); err != nil {
			return nil, fmt.Errorf("sync account %q config: %w", account.ID, err)
		}
	}
	return store, nil
}

func (s *Store) Root() string {
	return s.root
}

// SyncManagedConfig propagates desktop-managed configuration (including
// plugins, marketplaces, skills, and MCP server definitions) to every
// isolated subscription, together with Primary's project trust. Credential
// stores remain local to each account; syncIsolatedConfig excludes them.
func (s *Store) SyncManagedConfig() error {
	s.mu.RLock()
	accounts := slices.Clone(s.accounts)
	primaryCodexHome := s.primaryCodexHome
	s.mu.RUnlock()

	for _, account := range accounts {
		if samePath(account.CodexHome, primaryCodexHome) {
			continue
		}
		if err := syncIsolatedConfig(primaryCodexHome, account.CodexHome); err != nil {
			return fmt.Errorf("sync account %q config: %w", account.ID, err)
		}
	}
	return nil
}

func (s *Store) Accounts() []Account {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return slices.Clone(s.accounts)
}

func (s *Store) Account(id string) (Account, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, account := range s.accounts {
		if account.ID == id {
			return account, true
		}
	}
	return Account{}, false
}

func (s *Store) Controller() (Account, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, account := range s.accounts {
		if account.Controller {
			return account, true
		}
	}
	if len(s.accounts) == 0 {
		return Account{}, false
	}
	return s.accounts[0], true
}

func (s *Store) AddAccount(label string) (Account, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	label = strings.TrimSpace(label)
	if label == "" {
		label = s.nextDefaultLabelLocked()
	}
	id, err := randomID()
	if err != nil {
		return Account{}, err
	}
	codexHome := filepath.Join(s.root, "accounts", id, "codex-home")
	if err := os.MkdirAll(codexHome, 0o700); err != nil {
		return Account{}, fmt.Errorf("create account home: %w", err)
	}
	if err := os.Chmod(codexHome, 0o700); err != nil {
		return Account{}, fmt.Errorf("secure account home: %w", err)
	}
	if err := syncIsolatedConfig(s.primaryCodexHome, codexHome); err != nil {
		return Account{}, fmt.Errorf("write account config: %w", err)
	}

	account := Account{
		ID:        id,
		Label:     label,
		CodexHome: codexHome,
		Enabled:   true,
		CreatedAt: time.Now().Unix(),
	}
	s.accounts = append(s.accounts, account)
	if err := s.saveLocked(); err != nil {
		return Account{}, err
	}
	return account, nil
}

func (s *Store) UpdateAccount(id string, label *string, enabled *bool) (Account, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for index := range s.accounts {
		if s.accounts[index].ID != id {
			continue
		}
		if label != nil {
			trimmed := strings.TrimSpace(*label)
			if trimmed == "" {
				return Account{}, errors.New("account label cannot be empty")
			}
			s.accounts[index].Label = trimmed
		}
		if enabled != nil {
			s.accounts[index].Enabled = *enabled
		}
		if err := s.saveLocked(); err != nil {
			return Account{}, err
		}
		return s.accounts[index], nil
	}
	return Account{}, fmt.Errorf("account %q not found", id)
}

// nextDefaultLabelLocked returns the lowest unused "Subscription N" label.
// Counting accounts instead produced duplicate labels whenever a sign-in was
// abandoned or an account was removed.
func (s *Store) nextDefaultLabelLocked() string {
	used := make(map[string]struct{}, len(s.accounts))
	for _, account := range s.accounts {
		used[strings.ToLower(account.Label)] = struct{}{}
	}
	for number := 2; ; number++ {
		label := fmt.Sprintf("Subscription %d", number)
		if _, taken := used[strings.ToLower(label)]; !taken {
			return label
		}
	}
}

// RemoveAccount forgets a secondary subscription. The controller cannot be
// removed, and neither can an account that still owns chats: those chats
// would lose the only app-server that can resume them. The account's Codex
// home is moved under removed/ rather than deleted so it stays recoverable.
func (s *Store) RemoveAccount(id string) (Account, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	index := slices.IndexFunc(s.accounts, func(account Account) bool { return account.ID == id })
	if index < 0 {
		return Account{}, fmt.Errorf("account %q not found", id)
	}
	account := s.accounts[index]
	if account.Controller || samePath(account.CodexHome, s.primaryCodexHome) {
		return Account{}, errors.New("the primary subscription cannot be removed")
	}
	owned := 0
	for _, owner := range s.owners {
		if owner == id {
			owned++
		}
	}
	if owned > 0 {
		return Account{}, fmt.Errorf("%s still owns %d chat(s); pause it instead of removing it", account.Label, owned)
	}
	s.accounts = slices.Delete(s.accounts, index, index+1)
	if s.preferred == id {
		s.preferred = ""
	}
	if err := s.saveLocked(); err != nil {
		return Account{}, err
	}
	if err := s.retireAccountHome(account); err != nil {
		return account, err
	}
	return account, nil
}

func (s *Store) retireAccountHome(account Account) error {
	accountDir := filepath.Dir(account.CodexHome)
	expected := filepath.Join(s.root, "accounts", account.ID)
	if !samePath(accountDir, expected) {
		// Never move a directory the router did not create.
		return nil
	}
	if _, err := os.Stat(accountDir); errors.Is(err, os.ErrNotExist) {
		return nil
	}
	removedRoot := filepath.Join(s.root, "removed")
	if err := os.MkdirAll(removedRoot, 0o700); err != nil {
		return fmt.Errorf("create removed-accounts directory: %w", err)
	}
	destination := filepath.Join(removedRoot, fmt.Sprintf("%s-%d", account.ID, time.Now().Unix()))
	if err := os.Rename(accountDir, destination); err != nil {
		return fmt.Errorf("retire account home: %w", err)
	}
	return nil
}

// PreferredAccount returns the subscription new chats are pinned to, or ""
// for automatic routing.
func (s *Store) PreferredAccount() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.preferred
}

// SetPreferredAccount pins new chats to id; an empty id restores automatic
// routing.
func (s *Store) SetPreferredAccount(id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	id = strings.TrimSpace(id)
	if id != "" && !slices.ContainsFunc(s.accounts, func(account Account) bool { return account.ID == id }) {
		return fmt.Errorf("account %q not found", id)
	}
	if s.preferred == id {
		return nil
	}
	s.preferred = id
	return s.saveLocked()
}

func (s *Store) ThreadOwner(threadID string) (string, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	owner, ok := s.owners[threadID]
	return owner, ok
}

func (s *Store) SetThreadOwner(threadID, accountID string) error {
	if threadID == "" || accountID == "" {
		return errors.New("thread and account IDs are required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.owners[threadID] == accountID {
		return nil
	}
	s.owners[threadID] = accountID
	return s.saveLocked()
}

func (s *Store) ThreadCounts() map[string]int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	counts := make(map[string]int)
	for _, accountID := range s.owners {
		counts[accountID]++
	}
	return counts
}

func (s *Store) saveLocked() error {
	persisted := persistedState{
		Version:          stateVersion,
		Accounts:         s.accounts,
		ThreadOwner:      s.owners,
		PreferredAccount: s.preferred,
	}
	data, err := json.MarshalIndent(persisted, "", "  ")
	if err != nil {
		return fmt.Errorf("encode state: %w", err)
	}
	temporary := s.path + ".tmp"
	if err := os.WriteFile(temporary, append(data, '\n'), 0o600); err != nil {
		return fmt.Errorf("write state: %w", err)
	}
	if err := os.Chmod(temporary, 0o600); err != nil {
		return fmt.Errorf("secure state: %w", err)
	}
	if err := os.Rename(temporary, s.path); err != nil {
		return fmt.Errorf("commit state: %w", err)
	}
	return nil
}

func randomID() (string, error) {
	bytes := make([]byte, 8)
	if _, err := rand.Read(bytes); err != nil {
		return "", fmt.Errorf("generate account ID: %w", err)
	}
	return hex.EncodeToString(bytes), nil
}
