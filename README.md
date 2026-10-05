# Codex Subscription Router — maintained fork

Maintained by [Mvnshi](https://github.com/Mvnshi), based on
[b-nnett's original project](https://github.com/b-nnett/codex-subscription-router),
with community compatibility work and its original authorship preserved.

A free, open-source ChatGPT subscription router for multiple accounts: balance
usage, keep conversations moving, and manage subscriptions in one local desktop app.
See [branch status and credits](docs/MAINTAINED.md).

[Visit the website](https://mvnshi.github.io/codex-subscription-router/) ·
[Website source](website/)

Supports the recorded official macOS builds `6396` through `12246`. Build `8109` is
provisional: it patches, signs, launches and loads connected accounts, but
routing and failover have not been exercised on it. See
[the 8109 port notes](docs/BUILD-8109-PORT.md). Build `12246` patches, signs and
launches, and passes account/usage/profile/plugin screens, routing on three
accounts, failover and history-preserving account moves. Native desktop clicks,
keyboard input and real reset redemption through the account API also passed. See
[the 12246 port notes](docs/BUILD-12246-PORT.md). The Windows port supports the
Microsoft Store build `26.930.3930.0` (app build `12947`), provisionally: it
patches, launches beside the official app, and routes, switches and moves chats
between two real subscriptions (checked live); reset redemption, plugin scoping,
Computer Use and ARM64 are not yet exercised. See
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
| Platform: Windows | x64, **provisional**: tested against the Microsoft Store build below; every anchor is checked at run time and any other build needs `--allow-untested-source`. arm64 is untested |
| Official ChatGPT versions (macOS) | `26.803.61601` (build `6396`), `26.810.52044` (build `6662`), `26.901.22334` (build `7746`), `26.901.51231` (build `8109`), `26.928.20755` (build `12246`, provisional) |
| Official ChatGPT versions (Windows) | `OpenAI.Codex` `26.930.3930.0` from the Microsoft Store (app `26.930.31730`, build `12947`, provisional) |
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

- 64-bit Windows (x64 tested; ARM64 untested)
- The official ChatGPT/Codex desktop app installed from the **Microsoft Store**
  (the package `OpenAI.Codex`; OpenAI ships the Windows app only this way). The
  patcher finds it through the package registry, reads its files and copies them
  to a normal folder; the Store package itself is never modified.
- Go 1.26+ (`winget install --id GoLang.Go -e`)
- Node.js 22.12+ and npm (`winget install --id OpenJS.NodeJS.LTS -e`)
- Python 3.11+ (`winget install --id Python.Python.3.12 -e`)
- git (`winget install --id Git.Git -e`)

No signing identity is needed; the copy runs unsigned (see
[the security model](docs/SECURITY-MODEL.md)). Windows' default configuration
(long paths off) is enough: the copy is staged under a short directory name,
and if a path would still be too long the patcher says so before copying
anything.

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
> The Windows port is provisional. It is verified on the Microsoft Store build
> `26.930.3930.0` (patches, launches next to the official app, runs the
> multiplexer, connects accounts, and routes, switches and moves chats between
> two real subscriptions); reset redemption, plugin scoping, Computer Use and
> ARM64 are not yet exercised. A different Store build is refused until
> `--allow-untested-source` is passed, and every layout assumption is checked at
> run time instead of assumed. Read [the Windows port notes](docs/WINDOWS.md)
> first.

Run one command in PowerShell (5.1 or 7) as a normal user, not as
administrator. It downloads or updates the source, installs the locked build
dependencies, builds the independent copy, and launches it:

```powershell
irm https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/install.ps1 | iex
```

If the Store has updated the app to a build that is not recorded yet, the
patcher stops with "the source version, build, or app.asar hash is not
approved". The anchors are still checked, so you can opt in by setting the
override in the same session first:

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
instead of a partial installation when a check fails. When a tool is missing
the message lists the `winget install` command for each one; open a new
PowerShell window afterwards so `PATH` is refreshed. It does not check for the
official app itself; the patcher discovers and verifies that.

> [!TIP]
> To inspect the installer before running it, open
> [`install.ps1`](install.ps1) or download it without piping it into `iex`.

#### Install via prompt

> Install the maintained fork of Codex Subscription Router from `https://github.com/Mvnshi/codex-subscription-router/tree/main` on this Windows PC using the repository's supported PowerShell installer (`install.ps1`) from a normal, non-elevated PowerShell, without modifying the official ChatGPT app or deleting any existing router state. The Windows port is provisional: tell me before setting `CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE=1` (only needed if the Store app is on a build the project has not recorded), launch the app, confirm the official app and its `codex://` handler are unchanged, and ask me only if a prerequisite requires interaction.

#### Windows troubleshooting

Every message below is printed by the installer or patcher itself; none of them
leaves a half-installed copy behind.

| Message or symptom | What to do |
| --- | --- |
| `missing prerequisites: ...` | Run the `winget install` lines it prints, open a **new** PowerShell window (so `PATH` refreshes), rerun. |
| `expected exactly one official install, found 0` | Install the ChatGPT/Codex desktop app from the Microsoft Store, open it once, rerun. If it is installed somewhere unusual, pass `--source "<its app directory>"`. |
| `expected exactly one official install, found 2` | Two installs were found (for example Store and a manual copy). Pass `--source` with the one to use. |
| `the source version, build, or app.asar hash is not approved` | The Store updated the app to a build the project has not recorded. The anchors are still checked, so you can opt in with `CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE=1` (installer) or `--allow-untested-source` (patcher). |
| An error naming an anchor (`expected N ... found M`, `could not find ...`) | That Store build changed in a way the patch does not cover yet. Stop, keep the printed `Source ... version` line, and open an issue; do not loosen the check. |
| `quit the running app before replacing it` | Close Codex Subscription Router (the installer does this itself), then rerun. The official app can stay open. |
| `the copied app would contain paths of N characters` | Windows long paths are off and a path is still too long. Either enable `LongPathsEnabled` (elevated PowerShell, then sign out and in) or pass a short `--destination`, for example `C:\CSR`. |
| `this installer is running as administrator` | Rerun from a normal, non-elevated PowerShell. |
| SmartScreen warns about the app | Expected once: the copy is unsigned because its integrity resource is rewritten. |
| The router did not update after the Store updated the official app | Rebuild: rerun the installer (it replaces the copy and keeps a backup; accounts and chat ownership are kept). |
| Adding a second subscription | In the router, open the profile menu at the bottom of the sidebar, choose **Add another subscription**, and finish the device-code sign-in in the browser. |

#### Install from a clone

```powershell
git clone --branch main https://github.com/Mvnshi/codex-subscription-router.git
cd codex-subscription-router
npm ci --ignore-scripts
python scripts\patch_app_windows.py
& "$env:LOCALAPPDATA\Programs\Codex Subscription Router\Codex Subscription Router.exe"
```

(Add `--allow-untested-source` only if the Store app is on an unrecorded build.)

This creates:

- `%LOCALAPPDATA%\Programs\Codex Subscription Router\`, a full copy of the
  Store package's `app` directory (about 2 GB) containing `Codex Subscription Router.exe` (the
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
`python scripts\patch_app_windows.py --force` (plus `--allow-untested-source`
after a Store update to an unrecorded build). Because the copy is made from the
installed Store package, rebuild after the Store updates the official app to pick
the update up.

What the copy does not have, because the Store package declares it at install
time: the `codex://` handler and Explorer context menu (they stay with the
official app), the Store-managed Windows sandbox service, and package identity.
See [the Windows port notes](docs/WINDOWS.md) for what was observed.

Closing the router's window only hides it: the app keeps running in the background
(launching it again brings the window back). The copy currently gets no tray icon,
because Windows binds the app's fixed tray identity to the official app's
executable, so there is nothing to quit it from; to quit it fully, end its
processes in Task Manager (`ChatGPT.exe` and `codex.exe` under `Codex Subscription
Router`) or rerun the installer, which stops them for you. Each
rebuild keeps the previous copy as a backup of about 2 GB under
`%USERPROFILE%\.codex-mux\backups`; delete old ones when you no longer need them.

The router and the official app share `~/.codex`, so each lists the same chats,
but a chat runs on the engine of whichever app you send it from. Only the router's
own window routes between subscriptions; a chat you send from the official app
uses the official app's engine and its own account, and nothing the router does
changes that.

If your Codex config sends model traffic through a local gateway or another
provider (`model_provider` in `~/.codex/config.toml`), the router's per-account
homes inherit it. A gateway that forwards each engine's own login keeps the
accounts separate; one that substitutes its own credentials would put every
account's traffic behind one login. A gateway that answers a quota error by
switching provider also hides that error from the router, so it cannot fail a
chat over mid-turn. The router still moves a chat before sending when an
account's usage shows it is spent, and it reports "all connected subscriptions are
depleted" for a new chat when every subscription is spent, even if the gateway
could have served it. Remove the `model_provider` line if you want the router to
do all the switching.

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
| New chat | Assigned by quota-at-risk, banked resets, and short-window pressure — or to the pinned subscription while it has usage |
| Follow-up | Sent to the thread's persisted account owner |
| Primary depleted | Native usage surfaces show pooled usage; limit banners wait for the whole pool |
| Owner depleted | Continued through another account with capacity |
| Every account depleted | Combined quota alert with the next known reset |
| Account paused | Excluded from routing and pooled usable quota |
| Sign-in rejected | Excluded until it signs in again; refreshed once automatically |

The subscription assigned to the current thread appears in its pinned summary,
where **Continue this chat on** moves the chat to another subscription. Chats
live on this Mac, so every subscription sees the same local chats and projects.

To choose the subscription for new chats, open the profile menu and use
**New chats use** (or click a subscription row). Click it again, or choose
**Automatic**, to return to balanced routing. The Usage sheet shows each
subscription's plan, credits, and windows, with actions to refresh, pause, or
remove it.

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

The account menu offers **Usage resets: Ask me** (default) and **Use automatically**.
Ask me confirms every manual redemption and never spends a reset during routing.
Automatic mode is explicit opt-in: ordinary account failover comes first, then
a supported, unexpired reset can be used only when every usable account is
depleted. Automatic spending is serialized across accounts with a ten-minute
cooldown after a redemption request, including uncertain network outcomes.
Reset credits belong to their subscription and are never transferred.

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
only for an unrecorded build). The previous copy moves to a timestamped directory
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
`@electron/asar` and `resedit` are build-only. `npm run check` and
`npm run release:check` are for macOS and Linux: `check:python` and
`release:check` call `python3` and `check:shell` calls `bash`, which a stock
Windows machine does not provide. On Windows run the per-tool commands the
`windows` CI job runs instead (`py -3` in place of `python` if `python` is the
Microsoft Store placeholder):

```powershell
npm run check:go
npm run check:js
npm run check:win
python -m py_compile scripts/patch_app.py scripts/patch_app_windows.py scripts/check_release.py
python -m unittest discover -s scripts -p "test_*.py"
python scripts/check_release.py
```

`check:win`, the Windows PE helper tests, skips its compiled-fixture cases
with a printed reason when `go` is not on `PATH` (CI always has it), so run it
with Go installed for a meaningful result. `check:shell` (`bash -n install.sh`)
has no Windows equivalent; CI parses
`install.ps1` with the PowerShell parser instead. CI runs a `macos` and a
`windows` job. Deterministic UI preview routes are enabled only when
`CODEX_MUX_UI_TESTS=1` is present at launch and remain token-authenticated.

The signed-app test procedure is in [SMOKE-TEST.md](docs/SMOKE-TEST.md). The
latest completed run is recorded in
[E2E-REPORT-0.1.0.md](docs/E2E-REPORT-0.1.0.md).

## Known limitations

- Upstream ChatGPT updates can require new, reviewed patch anchors.
- The initial merged history fetch is limited to 500 threads per account.
- Combined “skills explored” totals can count the same skill once per account
  because the upstream profile response exposes counts rather than skill IDs.
- Generated macOS app bundles are tied to one macOS user and signing team.
- The Windows port is provisional: verified on one Store build to patch,
  launch, connect accounts, and route, switch and move chats between two real
  subscriptions, but reset redemption, plugin scoping and ARM64 have not been
  exercised, and the reactive failover (a turn the engine itself reports as over
  its limit) is covered only by automated tests. The Windows copy is unsigned, has no package identity
  (so no Store-managed sandbox service, `codex://` handler or Explorer menu).
  Computer Use on Windows is untested: the package ships a helper and the copy
  starts it, but the patcher does not re-identify it as it does on macOS.
- Releases are source-only; patched OpenAI binaries are never distributed.

## Contributing and releases

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. Releases use
the source-only process in [RELEASING.md](docs/RELEASING.md) and require a
completed signed-app smoke test for the exact tagged commit.

## License

Project source is available under the [MIT License](LICENSE). ChatGPT, Codex,
and the official desktop applications are OpenAI products and are not covered
by this license.
