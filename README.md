# Codex Subscription Router — maintained fork

Maintained by [Mvnshi](https://github.com/Mvnshi), based on
[b-nnett's original project](https://github.com/b-nnett/codex-subscription-router),
with community compatibility work and its original authorship preserved.
See [branch status and credits](docs/MAINTAINED.md).

Supports official macOS builds `6396` through `8109`. Build `8109` is
provisional: it patches, signs, launches and loads connected accounts, but
routing and failover have not been exercised on it. See
[the 8109 port notes](docs/BUILD-8109-PORT.md). A Windows port exists, but no
official Windows build has been exercised with it yet; see
[the Windows port notes](docs/WINDOWS.md).

![Multi-subscription account menu](screenshots/account-menu.png)

Use multiple ChatGPT subscriptions from one independent desktop app on macOS
or Windows.

Codex Subscription Router creates a locally patched copy of the official
ChatGPT app, balances new chats across connected subscriptions, and keeps every
thread on one subscription so follow-up turns retain conversation context and
benefit from account-level caching.

The official ChatGPT installation is used only as build input and is never
modified. This repository contains source code and build tooling—not OpenAI
binaries or a prebuilt application.

> [!WARNING]
> This is an unofficial, version-sensitive project. It is not affiliated with
> or supported by OpenAI. Review the source and ensure your use complies with
> the terms governing every connected subscription.

![Combined multi-account profile](screenshots/combined-profile-20px.png)

## Highlights

- **Quota-aware routing.** New chats favour weekly allowance that will expire
  sooner, with a bounded boost for accounts holding banked usage resets.
- **Sticky conversations.** Once a thread is assigned, every follow-up returns
  to the same subscription unless that subscription is depleted.
- **Automatic failover.** A depleted thread continues through another account
  with quota; if the whole pool is empty, the app shows one combined alert.
- **Native account management.** The existing profile menu shows pooled usage,
  profile photos, plan names, masked emails, and device-code sign-in.
- **Account-aware settings.** Profile statistics can be viewed together or per
  subscription, while the Plugins page can switch Apps and MCP connections
  between accounts.
- **Per-account resets.** The native rate-limit sheet shows and consumes resets
  for the selected subscription.
- **Working macOS integrations.** The copied Appshots and Computer Use helper is
  independently identified and signed so it can receive its own privacy grants.

## How it works

The patched desktop still opens one app-server connection. A small Go
multiplexer fans that connection out to one official Codex child per account.
Each child has an isolated Codex home, while the multiplexer records the owner
of every thread. On Windows the same layout is installed as a plain directory
(`Codex Subscription Router.exe`, `codex.exe`, `codex.real.exe`).

```text
Codex Subscription Router.app
        │
        │ one app-server connection
        ▼
    codex-mux
    ├── Primary       → ~/.codex
    ├── Subscription 2 → isolated Codex home
    └── Subscription 3 → isolated Codex home
             │
             └── thread ID → persistent account owner
```

New-thread routing compares the quota burn rate needed before each weekly reset,
then applies a capped banked-reset boost. Short-window usage, pinned-thread
count, and stable account order break close results. Existing threads do not
migrate merely for load balancing.

Read [the architecture](docs/ARCHITECTURE.md) for the request flow and
[the security model](docs/SECURITY-MODEL.md) for trust boundaries.

## Compatibility

Codex Subscription Router currently targets:

| Component | Supported value |
| --- | --- |
| Platform: macOS | Apple silicon, tested against the builds below |
| Platform: Windows | x64 and arm64, **provisional**: no official Windows build has been exercised yet; every anchor is checked at run time and `--allow-untested-source` is required until a build is recorded |
| Official ChatGPT versions (macOS) | `26.803.61601` (build `6396`), `26.810.52044` (build `6662`), `26.901.22334` (build `7746`), `26.901.51231` (build `8109`) |
| Official ChatGPT versions (Windows) | none recorded yet |
| Go | 1.26 or newer |
| Node.js | 22.12 or newer |
| Python | 3 on macOS; 3.11 or newer on Windows |

The patcher verifies the official version, build, ASAR hash, renderer anchors,
and native binary constants before changing anything. An unknown upstream build
is rejected by default rather than being partially patched. See
[Compatibility](docs/COMPATIBILITY.md) for the recorded hash and test details,
and [the Windows port notes](docs/WINDOWS.md) for what the Windows patcher
checks at run time.

## Requirements

### macOS

- The official ChatGPT app installed at `/Applications/ChatGPT.app`
- Xcode Command Line Tools
- Go 1.26+
- Node.js 22.12+ and npm
- An Apple Development or Developer ID Application signing identity

A team-backed signing identity is required for reliable Appshots and Computer
Use permissions. Ad-hoc signing is intended only for diagnostics.

### Windows

- 64-bit Windows (x64 or ARM64)
- The official ChatGPT/Codex desktop app installed per user from the
  downloadable installer. Microsoft Store/MSIX installs are not supported;
  they cannot be copied.
- Go 1.26+
- Node.js 22.12+ and npm
- Python 3.11+
- git

No signing identity is needed; the copy runs unsigned (see
[the security model](docs/SECURITY-MODEL.md)). If the official install has
long paths, Windows long-path support (`LongPathsEnabled`) may be required;
the patcher says so before copying anything.

## Install

The commands in this section are for macOS; Windows follows in
[Install on Windows](#install-on-windows).

Run one command. It downloads or updates the source, installs the locked build
dependency, creates the independently signed app, and launches it:

```sh
curl -fsSL https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/install.sh | /bin/bash
```

The installer keeps its source checkout in
`~/.codex-subscription-router-mvnshi/source`. On an existing installation it uses the
same account state, creates a recoverable backup, and requires signing-team
continuity so macOS privacy grants remain valid. It stops with a clear message
instead of making a partial installation when a prerequisite or upstream
compatibility check fails.

> [!TIP]
> To inspect the installer before running it, open
> [`install.sh`](install.sh) or download it without piping it into a shell.

### Install via prompt

> Install the maintained fork of Codex Subscription Router from `https://github.com/Mvnshi/codex-subscription-router/tree/main` on this Mac using the repository's supported one-command installer, without modifying the official ChatGPT app or deleting any existing router state. Verify the resulting app and Computer Use helper signatures, launch the app, and ask me only if a prerequisite or macOS permission requires interaction.

### Install from a clone

```sh
git clone --branch main https://github.com/Mvnshi/codex-subscription-router.git
cd codex-subscription-router
npm ci --ignore-scripts
python3 scripts/patch_app.py
open "$HOME/Applications/Codex Subscription Router.app"
```

This creates:

- `~/Applications/Codex Subscription Router.app`
- `~/Applications/Codex Subscription Router Computer Use.app`
- an independent desktop profile under
  `~/Library/Application Support/Codex Subscription Router`

The first valid Developer ID Application identity is selected, falling back to
an Apple Development identity. Select a certificate explicitly when needed:

```sh
CODEX_MUX_SIGNING_IDENTITY="Developer ID Application: Example Corp (TEAMID1234)" \
  python3 scripts/patch_app.py
```

The patcher resolves the signing team from a temporary signed executable, so
Apple Development identities whose display-name suffix differs from their team ID
are supported. `CODEX_MUX_SIGNING_IDENTITY` also accepts a certificate fingerprint.

Reuse the same Apple team for every rebuild. Changing teams changes the app's
designated requirement and can invalidate existing macOS privacy consent. The
patcher refuses an unexpected team change unless you deliberately pass
`--allow-signing-team-change`.

For diagnostic builds without a certificate:

```sh
python3 scripts/patch_app.py --allow-adhoc-signing
```

Appshots and Computer Use may not function with an ad-hoc signature.

### Install on Windows

> [!NOTE]
> The Windows port is provisional. No official Windows build of the ChatGPT
> desktop app has been exercised with it, so the patcher refuses every source
> until `--allow-untested-source` is passed, and it checks every layout
> assumption at run time instead of assuming it. Read
> [the Windows port notes](docs/WINDOWS.md) first.

Run one command in PowerShell (5.1 or 7) as a normal user, not as
administrator. It downloads or updates the source, installs the locked build
dependencies, builds the independent copy, and launches it:

```powershell
irm https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/install.ps1 | iex
```

Until a Windows build is recorded as supported, the patcher stops with
"the source version, build, or app.asar hash is not approved". Set the override
in the same session first:

```powershell
$env:CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE = '1'
irm https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/install.ps1 | iex
```

`iex` cannot take parameters, so every option is an environment variable:

| Variable | Effect |
| --- | --- |
| `CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR` | Source checkout to use or create (default `%USERPROFILE%\.codex-subscription-router-mvnshi\source`) |
| `CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE=1` | Pass `--allow-untested-source` to the patcher |
| `CODEX_SUBSCRIPTION_ROUTER_NO_LAUNCH=1` | Build without launching the app |

From a downloaded copy or a clone the same options are parameters:
`powershell -ExecutionPolicy Bypass -File .\install.ps1 [-SourceDir <path>] [-AllowUntestedSource] [-NoLaunch]`.

The installer refuses elevated sessions, checks git, Go 1.26+, Node.js 22.12+,
npm, and Python 3.11+ (`python`, then `py -3`), stops the copy's processes and
passes `--force` on an existing installation, and stops with a clear message
instead of a partial installation when a check fails. It does not check for
the official app itself; the patcher discovers and verifies that.

> [!TIP]
> To inspect the installer before running it, open
> [`install.ps1`](install.ps1) or download it without piping it into `iex`.

#### Install via prompt

> Install the maintained fork of Codex Subscription Router from `https://github.com/Mvnshi/codex-subscription-router/tree/main` on this Windows PC using the repository's supported PowerShell installer (`install.ps1`) from a normal, non-elevated PowerShell, without modifying the official ChatGPT app or deleting any existing router state. The Windows port is provisional: tell me before setting `CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE=1`, launch the app, confirm the official app and its `codex://` handler are unchanged, and ask me only if a prerequisite requires interaction.

#### Install from a clone

```powershell
git clone --branch main https://github.com/Mvnshi/codex-subscription-router.git
cd codex-subscription-router
npm ci --ignore-scripts
python scripts\patch_app_windows.py --allow-untested-source
& "$env:LOCALAPPDATA\Programs\Codex Subscription Router\Codex Subscription Router.exe"
```

This creates:

- `%LOCALAPPDATA%\Programs\Codex Subscription Router\`, a full copy of the
  official install directory containing `Codex Subscription Router.exe` (the
  launcher), the multiplexer as `codex.exe`, and the original as
  `codex.real.exe`
- an independent desktop profile under `%APPDATA%\Codex Subscription Router`
- router state, backups, and the control token under `%USERPROFILE%\.codex-mux`
- a Start menu shortcut (`--no-shortcut` skips it)

The patcher prints the source identity (`ProductVersion`, `FileVersion`,
architecture, signature state, and `app.asar` SHA-256) before changing
anything; `--source`, `--electron-executable`, and `--codex-executable`
override its discovery when the official layout differs. The copy is unsigned
because rewriting the executable's asar-integrity resource drops the
Authenticode signature; SmartScreen may warn once. Rebuild with
`python scripts\patch_app_windows.py --force`.

## Grant macOS permissions

Open **System Settings → Privacy & Security** and grant:

| Permission | Application |
| --- | --- |
| Accessibility | Codex Subscription Router |
| Screen & System Audio Recording | Codex Subscription Router Computer Use |

When macOS offers **Quit & Reopen**, use it. If the app does not relaunch,
reopen Codex Subscription Router manually. If the Computer Use row does not
appear, press the plus button and choose
`~/Applications/Codex Subscription Router Computer Use.app`.

Do not select the official ChatGPT or Codex Computer Use helper for this build;
the independent app has its own identity and permission rows. macOS may also
request Automation access the first time Computer Use controls another app.

## Add subscriptions

1. Open the profile menu at the bottom of the sidebar.
2. Select **Add another subscription**.
3. Complete the displayed device-code sign-in in your browser.
4. Return to Codex Subscription Router and wait for the account row to appear.

While the code is visible, clicking away does not dismiss the menu. Clicking
the code copies it and opens the verification page.

The profile menu displays combined weekly usage followed by one row per
subscription. Email addresses remain masked until hovered. The final row always
starts another sign-in.

## Routing behavior

| Situation | Behaviour |
| --- | --- |
| New chat | Assigned by quota-at-risk, banked resets, and short-window pressure |
| Follow-up | Sent to the thread's persisted account owner |
| Primary depleted | Native usage surfaces show pooled usage; limit banners wait for the whole pool |
| Owner depleted | Continued through another account with capacity |
| Every account depleted | Combined quota alert with the next known reset |
| Account disabled | Excluded from routing and pooled usable quota |

The subscription assigned to the current thread appears in its pinned summary.

## Profiles, plugins, and resets

**Profile statistics** begin in a combined view with overlapping account
photos. Select a photo to see only that subscription's identity and statistics;
select it again to return to the combined view.

**Settings → Plugins** includes a subscription picker. Plugin definitions and
managed MCP configuration are shared, while Apps, connection status, and OAuth
login are scoped to the selected subscription.

**Rate-limit resets** remain native to the app, with an account picker added to
the sheet. Selecting a subscription changes the displayed balance and ensures
the reset is consumed only for that account.

![Account-scoped plugin connections](screenshots/plugin-account-picker-secondary-final.png)

## Update or rebuild

The copied app's updater is disabled so an official update cannot overwrite the
patch. Update `/Applications/ChatGPT.app`, verify that the new build is listed
as compatible, then rebuild:

```sh
python3 scripts/patch_app.py --force
```

Quit Codex Subscription Router and its Computer Use helper first. Existing
destinations are moved to timestamped directories under `~/.codex-mux/backups`;
account state and credentials are stored outside the app bundle and remain
intact. Delete old backups manually after the rebuilt app passes the smoke test.

Build separately for each macOS user. Generated bundles contain user-specific
helper and socket paths and are not relocatable or intended for redistribution.

On Windows, quit Codex Subscription Router, then run
`python scripts\patch_app_windows.py --force` (with `--allow-untested-source`
until a build is recorded). The previous copy moves to a timestamped directory
under `%USERPROFILE%\.codex-mux\backups`; the same state and credential rules
apply.

## Local data and security

| Path | Purpose |
| --- | --- |
| `~/.codex` | Primary credentials, conversations, and cache |
| `~/.codex-mux/state.json` | Account metadata and sticky thread ownership |
| `~/.codex-mux/accounts/<id>/codex-home` | Isolated secondary account data |
| `~/.codex-mux/control-token` | Token for the loopback-only control service |
| `~/.codex-mux/backups` | Recoverable app and helper backups |
| `~/Library/Application Support/Codex Subscription Router` | Independent desktop profile (macOS) |
| `%APPDATA%\Codex Subscription Router` | Independent desktop profile (Windows) |

On Windows, `~` is `%USERPROFILE%`. The state root is protected with an NTFS
ACL (current user and SYSTEM only) set by the patcher; the copy itself is
unsigned.

The control service binds only to `127.0.0.1` and protects private routes with a
random 256-bit token. OAuth tokens stay inside their account's Codex home and
are never returned by the control API. Account directories are owner-only.

Plugin configuration is intentionally synchronized from the Primary account.
Inline secrets inside shared MCP configuration are therefore copied to each
isolated account home; the account homes are not separate secret boundaries.

See [SECURITY.md](SECURITY.md) before reporting a credential, signing, or local
control-service issue.

## Development and verification

```sh
npm ci --ignore-scripts
npm run check
npm run release:check
```

The Go backend and injected renderer have no runtime third-party dependencies.
`@electron/asar` and `resedit` are build-only. `npm run check` runs on macOS,
Linux, and Windows and includes the Windows PE helper tests (`check:win`), which
need `go` on `PATH`. CI runs a `macos` and a `windows` job. Deterministic UI
preview routes are enabled only when `CODEX_MUX_UI_TESTS=1` is present at
launch and remain token-authenticated.

The signed-app test procedure is in [SMOKE-TEST.md](docs/SMOKE-TEST.md). The
latest completed run is recorded in
[E2E-REPORT-0.1.0.md](docs/E2E-REPORT-0.1.0.md).

## Known limitations

- Upstream ChatGPT updates can require new, reviewed patch anchors.
- The initial merged history fetch is limited to 500 threads per account.
- Combined “skills explored” totals can count the same skill once per account
  because the upstream profile response exposes counts rather than skill IDs.
- Generated macOS app bundles are tied to one macOS user and signing team.
- The Windows port has never been exercised against an official Windows
  build; the Windows copy is unsigned and there is no Windows Computer Use
  helper.
- Releases are source-only; patched OpenAI binaries are never distributed.

## Contributing and releases

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. Releases use
the source-only process in [RELEASING.md](docs/RELEASING.md) and require a
completed signed-app smoke test for the exact tagged commit.

## License

Project source is available under the [MIT License](LICENSE). ChatGPT, Codex,
and the official desktop applications are OpenAI products and are not covered
by this license.
