# Signed-app smoke test

Complete this checklist on the exact official build recorded in
`docs/COMPATIBILITY.md` before publishing a release draft. The sections up to
and including Appshots and Computer Use are the macOS procedure: use a
team-backed signature and reuse the same Apple team as the previous installed
build. The Windows checklist follows at the end.

## Build and identity

- Confirm the patcher reports the expected version, build, and ASAR SHA-256.
- Verify the official `/Applications/ChatGPT.app` is unchanged.
- Verify the app and every nested Computer Use application with
  `codesign --verify --deep --strict`.
- Confirm the installed app and helper report the intended bundle IDs and the
  same `TeamIdentifier`.

## Accounts and routing

- Connect at least two subscriptions and confirm photos, plans, masked emails,
  pooled usage, and loading states.
- Start chats until each account has received one; confirm every follow-up stays
  on its original account.
- Spoof one depleted account and confirm the thread continues on an account with
  quota. Spoof all accounts depleted and confirm the combined alert.
- Open a quota-triggered reset sheet, switch subscriptions, consume a reset, and
  confirm only the selected account changes.

## Settings and plugins

- Confirm Profile opens in the combined state, uses 20 px avatar overlap, and
  toggles between combined and per-account statistics.
- In Settings → Plugins, select each subscription and verify Apps, MCP status,
  and MCP OAuth login reflect that account while installed definitions remain
  shared.

## Appshots and Computer Use

- In System Settings, grant Accessibility to Codex Subscription Router and
  Screen & System Audio Recording to Codex Subscription Router Computer Use.
  Quit and reopen when macOS asks.
- Capture an Appshot from the attachment menu and with the Command-key shortcut.
- Run a Computer Use task and confirm the native helper performs the action
  without falling back to `osascript`.
- Rebuild once with the same signing team and confirm existing permissions still
  work without adding duplicate permission rows.

Record the tested commit, macOS version, signing team ID, and any deviations in
the release draft before publishing it.

## Windows

No Windows build is recorded yet, so this checklist is also the procedure that
records the first one (see [WINDOWS.md](WINDOWS.md)). Run it as a normal user
on a PC with the official ChatGPT desktop app installed per user, with the
official app closed before the build and running again for the side-by-side
checks.

### Build and identity

- Run `python scripts\patch_app_windows.py --allow-untested-source` (or
  `install.ps1` with `CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE=1`) and
  keep the full output. Confirm it printed the identity line
  `Source <ProductName> version: <ProductVersion> (file <FileVersion>), <arch>,
  <signed|unsigned>, app.asar <sha256>`, `Multiplexer installed at <path>`,
  `Retargeted N protocol-client registration(s)` (a warning instead means the
  handler check below is mandatory), and either
  `Recorded asar header digest <hex>` or the "no embedded asar integrity
  resource" line.
- Verify the official installation is unchanged: `Get-FileHash` of its
  Electron executable and `resources\app.asar` before and after the build are
  identical, and the official app still starts.
- Run `node scripts\win\exe-info.mjs` on the copied Electron executable and
  confirm `signed` is `false` and, when the resource exists, the
  `resources\app.asar` entry's `value` equals the recorded header digest.
- Confirm the destination contains `Codex Subscription Router.exe`, the
  multiplexer as `codex.exe`, and the original as `codex.real.exe` in the same
  directory.

### Launch and isolation

- Start `Codex Subscription Router.exe` while the official app is running.
  Both windows must be open at once; `Get-Process` shows the copy's processes
  under `%LOCALAPPDATA%\Programs\Codex Subscription Router`, one `codex.exe`
  (the multiplexer) and one `codex.real.exe` per enabled account.
- Confirm `%APPDATA%\Codex Subscription Router` was created and the official
  app's profile directory was not modified.
- Confirm the `codex://` handler still points at the official app:
  `Get-ItemProperty 'HKCU:\Software\Classes\codex\shell\open\command'`
  names the official executable, and if
  `HKCU:\Software\Classes\codex-subscription-router\shell\open\command`
  exists it names the copy.
- Confirm the Start menu shortcut and a taskbar pin open the copy, and that
  a second launch focuses the running copy instead of starting another.
- Quit the copy and confirm no `codex.exe` or `codex.real.exe` from the
  destination remains after a few seconds (Windows shutdown closes each
  child's stdin and kills it after two seconds).

### Accounts, routing, resets, and plugins

- Complete the macOS sections "Accounts and routing" and "Settings and
  plugins" unchanged. Launch alone is not evidence: routing across accounts,
  failover, thread resume, and per-account resets must each be exercised.

### Rebuild

- Rebuild with `--force` while the copy is closed (or through `install.ps1`,
  which stops the copy's processes first). Confirm the previous copy moved to
  `%USERPROFILE%\.codex-mux\backups\<timestamp>\` and that accounts and
  thread ownership survived.

There is no Computer Use section on Windows; the helper is macOS-only.

Record the tested commit, Windows build number (`winver` or
`[Environment]::OSVersion.Version`), architecture, official `ProductVersion`,
`FileVersion`, whole-file `app.asar` SHA-256, whether the integrity resource
existed, the Electron executable name, the `codex.exe` path, and the Go, Node.js,
and Python versions in the release draft, then add the build as described in
[WINDOWS.md](WINDOWS.md).
