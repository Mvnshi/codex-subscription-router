# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- Reset balances distinguish loading from failed requests and update each
  subscription independently. The Usage sheet and picker share pending requests.

### Added

- Provisional ChatGPT build `12246` support: split renderer chunks, packaged
  Codex CLI layout, and enabled Electron integrity-dictionary digest updates.
  Signing, isolated launch and account/usage/profile/plugin screens verified;
  fixes subscription selection through the lazy Usage sheet. Routing on three
  accounts, depletion failover and history-preserving account moves verified.
  Native desktop clicks and keyboard input and real reset redemption through
  the account API verified; the native redemption-button flow remains untested.
- Account-local history transfer for newer paginated engines, including shutting
  down a previously loaded target writer before replacing its stale history.
- Finished turns now clear the active-response marker so manual account switching
  works after a response completes.
- Choose which subscription new chats use: **New chats use** in the profile
  menu (or click an account row) pins new chats to one subscription while it
  has usage, falling back to automatic routing when it is depleted, paused, or
  signed out. Exposed as `GET/PUT /v1/routing`.
- **Continue this chat on** in a chat's Subscription summary moves an existing
  local chat to another subscription (`POST /v1/thread-account`).
- Credit balances per subscription in the menu, the Usage sheet, and tooltips,
  plus per-window usage details (5-hour and weekly, with reset times).
- Usage sheet actions per subscription: use for new chats, refresh plan and
  usage, pause, and remove (`POST /v1/accounts/{id}/refresh`,
  `DELETE /v1/accounts/{id}`; removed homes are moved to
  `~/.codex-mux/removed`).
- Paused, expired, and unfinished sign-ins are listed in the menu with a
  one-click fix.
- Compatibility with official ChatGPT `26.810.52044` (build `6662`).
- One-command installer with safe source updates, prerequisite checks, signed
  rebuilds, recoverable upgrades, and automatic launch.
- Reset-aware routing that prioritizes weekly quota at risk of expiring and
  gives a bounded boost to subscriptions with banked usage resets.
- Provisional Windows support: `install.ps1`; `scripts/patch_app_windows.py`
  (run-time discovery of the official install with exact, fail-closed checks,
  `codex.exe`/`codex.real.exe`, `%APPDATA%` profile isolation, `icacls`
  state hardening, `codex-subscription-router://` scheme retargeting); the Go
  launcher `cmd/codex-router-launcher`, built as
  `Codex Subscription Router.exe`; Windows child shutdown (stdin EOF, then
  kill) and
  `codex.real.exe` lookup in the multiplexer; the Node PE helpers
  `scripts/win/exe-info.mjs` and `scripts/win/set-asar-integrity.mjs` for the
  `INTEGRITY`/`ELECTRONASAR` resource, on `resedit` 3.1.0 added as an
  exact-pinned dev dependency; a `windows` CI job; and
  `docs/WINDOWS.md`. No official Windows build has been exercised yet, so
  `--allow-untested-source` is required until one is recorded.

### Fixed

- Plan upgrades now show immediately: the plan comes from the live usage
  endpoint instead of the cached ID token, and a mismatch triggers a
  background token refresh so Codex itself sees the new plan.
- A subscription whose sign-in ChatGPT rejected (`401`/`token_invalidated`,
  common after a plan change) was still counted as connected and could be
  routed chats; it is now flagged, excluded from routing and pooled usage, and
  refreshed once automatically before asking to sign in again.
- "Add another subscription" reuses an unfinished sign-in slot instead of
  creating another one, and default labels no longer repeat
  ("Subscription 3" ×3).
- The merged chat list no longer reassigns a chat's owner to whichever account
  answered last, and a chat listed by several accounts after failover appears
  once.
- A follow-up in a chat whose owner's five-hour window is spent now moves
  immediately instead of failing first.
- Every subscription inherits the Primary account's trusted projects, so local
  projects behave the same whichever account runs the chat.
- The installer uses a supported nvm-installed Node.js when the shell default
  is older than 22.12.

- Empty `patch_arguments` expansion in `install.sh` under bash 3.2 `set -u`.
- Slow profile-photo requests no longer block the first subscription list from
  showing connected accounts.
- Profile menus now dismiss normally on outside clicks and Escape after an
  additional subscription sign-in.
- Native usage surfaces (limit banner, sidebar usage alert, reset prompts)
  now reflect pooled usage, so a depleted Primary account no longer triggers
  them while another connected subscription still has weekly capacity.

### Changed

- The account menu, Usage sheet, and Plugins picker open with the last known
  subscriptions and usage and refresh in place instead of showing a connecting
  state on every open.
- The copied app can no longer start Sparkle through the renderer's update
  gate or the Check for Updates menu item, so it does not offer to replace
  itself with an unpatched official build.
- `@electron/asar` 4.2.1 → 4.3.0, `actions/checkout` 6.1.0 → 7.0.1, and
  `actions/setup-node` 6.5.0 → 7.0.0.
- The release check now requires every npm dev dependency (from `package.json`
  or the lock file's root entry) to be exact and lock-matched, requires
  `@electron/asar` and `resedit` to be declared, rejects runtime
  `dependencies` in `package.json`, checks `install.sh`'s executable bit
  through git's index mode, and rejects tracked `.exe`, `.lnk`, `.msi`, and
  `.msix` files.
- CI runs a `macos` job (the former `checks` job, renamed; a required
  status check named `checks` must be updated to `macos`) and a `windows`
  job; the macOS job also cross-compiles the Go packages for Windows.

## [0.1.0] - 2026-08-15

### Added

- Multi-subscription routing with quota-aware balancing and sticky threads.
- Account isolation, device-code sign-in, pooled usage, and quota failover.
- Native account menu, masked emails, plan labels, and profile photos.
- Combined Profile statistics with per-account selection.
- Account-scoped Apps and MCP connection state in Settings → Plugins.
- Per-account rate-limit reset selection and pooled depletion handling.
- Independently signed Appshots and Computer Use support.
- Fail-closed upstream compatibility checks and deepest-first nested helper signing.
- Loopback-only, token-authenticated diagnostic UI states.
- Source-only CI, draft release automation, security documentation, and smoke tests.

[Unreleased]: https://github.com/b-nnett/codex-subscription-router/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/b-nnett/codex-subscription-router/releases/tag/v0.1.0
