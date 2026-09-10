# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
  `INTEGRITY`/`ELECTRONASAR` resource; a `windows` CI job; and
  `docs/WINDOWS.md`. No official Windows build has been exercised yet, so
  `--allow-untested-source` is required until one is recorded.

### Fixed

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
- `resedit` 3.1.0 added as an exact-pinned dev dependency for the Windows
  integrity-resource rewrite. The release check now requires every npm dev
  dependency to be exact and lock-matched, checks `install.sh`'s executable
  bit through git's index mode, and rejects tracked `.exe`, `.lnk`, `.msi`,
  and `.msix` files.
- CI runs a `macos` job and a `windows` job; the macOS job also
  cross-compiles the Go packages for Windows.


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
