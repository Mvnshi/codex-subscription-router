# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Add persisted usage-reset policy, defaulting to Ask me. Manual redemption
  requires confirmation; automatic redemption is opt-in, follows account
  failover, and prevents concurrent or uncertain requests from draining credits.

### Setup and launch

- Make installing possible without knowing what a terminal is. `install.ps1` offers to install
  Go, Node.js and Python with winget when they are missing and picks up the new `PATH` without
  opening a new window, no longer needs Git (it downloads the source as a zip when Git is
  absent), and asks in plain words before building from a Codex build the project has not
  recorded. The patchers gained `--check-source` and exit code 3 for that case so the installer
  can ask before it stops or builds anything. `install.sh` got the same questions, offers
  Homebrew and Apple's Command Line Tools installer for missing tools, and offers a basic
  (ad-hoc) signature on a Mac with no Apple certificate. Where nobody can answer, the matching
  environment variable (`CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES` and friends) does.
- Windows: the Start menu shortcut is now named **Codex Router** and is also placed on the
  Desktop (`--no-desktop-shortcut` skips it); the shortcut that older versions made is removed
  when it points at the same copy. The copy gets the router's own icons for the shortcuts, the
  window and the notification area (`assets/windows`, drawn by `scripts/make_icons.py` from the
  website mark) so it can be told apart from the official app.
- Add `install-windows.cmd` and `install-mac.command` for a double-click install from a downloaded
  copy, `scripts/uninstall.ps1` (with `-WhatIf`, `-RemoveAccounts`, `-RemoveBackups`),
  read-only `scripts/doctor.ps1` and `scripts/doctor.sh` whose output is safe to paste into an
  issue, and `AGENTS.md` for AI assistants asked to install or work on the project.
- CI runs the installer logic on both shells that matter (Windows PowerShell 5.1 and 7, and
  macOS's bash 3.2), the uninstaller against a sandbox profile, and the doctor scripts.

### Windows

- Plugin requests scoped to an account that was removed or paused now fail with an
  error instead of being answered by Primary, which could show or start an OAuth
  login for the wrong account. Covered by new end-to-end tests that send scoped
  `mcpServerStatus/list`, `app/list` and `mcpServer/oauth/login` requests through
  the real multiplexer to two engines.
- Build the multiplexer and launcher for the architecture of the official host
  executable (x64 or ARM64, anything else stops the install) instead of the
  toolchain's default, and add a `windows-arm64` CI job that runs the Go tests on a
  real ARM64 Windows runner and checks both programs are ARM64. The macOS job now
  cross-compiles for arm64 as well.
- Stop setting the two Computer Use environment variables on Windows. The Store
  build reads them only on macOS and talks to its Computer Use helper over stdio,
  so they did nothing while implying a pinned pipe. Computer Use is off in OpenAI's
  Windows build unless `CODEX_ELECTRON_ENABLE_WINDOWS_COMPUTER_USE=1` is set; the
  copy inherits the environment unchanged.
- Verify the usage sheet's per-account reset flow and the Plugins page's account
  picker in the live Windows window (simulated reset balances, so no real credit
  was spent), and fix the UI-test bridge's account-row matcher for plan names that
  end in a letter.
- Add an opt-in engine provider override for the router: `CODEX_MUX_ENGINE_PROVIDER`
  or `<state root>/engine-provider` starts the router's engines with
  `-c model_provider=<id>`, so a Codex config that routes through a gateway which
  answers quota errors itself no longer hides them from the router. Off by default,
  only the router's engines are affected, and an invalid id stops the router with a
  message naming its source.
- The Windows port now works against the Microsoft Store build of the Codex
  desktop app, the only way OpenAI distributes it. The patcher finds the package
  through the package registry, reads its manifest to pick the host executable,
  and copies the `app` directory read-only instead of refusing `WindowsApps`.
  Verified on `OpenAI.Codex` `26.930.3930.0` (app build `12947`): patches,
  launches beside the official app, runs the multiplexer and connects accounts;
  recorded as provisional. Routing, manual moves and failover were then
  verified live between two real subscriptions (12 of 12 checks), together with
  the one-line installer's forced rebuild, shutdown and single-instance behaviour;
  reset redemption, plugin scoping, Computer Use and ARM64 remain unexercised.
- Add end-to-end failover tests that run the real multiplexer against real child
  processes and files on every OS: new chats avoid a spent account, a pin on a
  spent account falls back, a turn is moved before it is sent or replayed after a
  usage-limit kill or rejection with its history intact, and follow-ups stay put.
- Document how a provider gateway that switches provider on quota errors interacts
  with the router, and that closing the window keeps the app running in the
  background.
- Give the Windows copy its own tray-icon identity. The app registers its tray
  icon with a fixed GUID that Windows binds to the first executable to use it, so
  the copy had no tray icon and closing its window left it running with nothing to
  quit it from. Each known GUID is replaced in an exact anchor; a repeated literal
  stops the patch, and finding none only warns.
- Repacking reproduces the official archive's unpacked set exactly, derived from
  `asar list --is-pack`, instead of unpacking whole top-level packages. The old
  pattern unpacked nested JavaScript and produced a directory path over Windows'
  limit; the repack is now verified for an identical unpacked set.
- The copy is staged under a short directory name so the default Windows
  configuration (long paths off) is enough.
- Accept the Store build's win32-guarded protocol registration (it never runs on
  Windows) in an exact anchor, and let the Usage sheet anchor take the minifier's
  name for the compiler's memo-cache array. Other registrations still fail closed.
- `install.ps1` names the `winget install` command for each missing prerequisite.
- The patcher streams the `app.asar` hash instead of reading 550 MB into memory.
- Go test `TestTransferredThreadCompatibilityAndRefresh` no longer asserts a POSIX
  file mode on Windows.

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
