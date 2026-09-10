# Porting notes: Windows

Status in one line: implemented and unit-tested on Linux and in CI; never
launched against an official Windows build of the ChatGPT desktop app.

No official Windows build was available while this port was written. Every
statement below about the official Windows app is a run-time check the patcher
performs, not an observation, and the port stays provisional until the
procedure in [Recording the first supported build](#recording-the-first-supported-build)
has been completed once. Nothing here weakens an anchor: where the Windows
layout is unknown, the patcher stops and names what it found.

## Method

`scripts/patch_app.py` was split, byte-identically for macOS, into the pieces
that are platform-neutral and the pieces that are macOS-only. The Python tests
hold exact expectations (bundle contents, error messages, side-effect order)
written against the pre-split code, and they pass unchanged after it.
`scripts/patch_app_windows.py` imports the neutral pieces and adds Windows
equivalents for the rest. Each equivalent follows the rule the macOS anchors
follow: an exact check with an exact expected count, and an error naming what
was found when it fails. Where the official Windows layout could not be known
in advance, the value is discovered at run time and can be overridden
explicitly instead of being assumed:

- the official install directory (`--source`),
- the name of the Electron executable (`--electron-executable NAME`),
- the location of the bundled `codex.exe` (`--codex-executable RELPATH`),
- the packages the official build keeps unpacked beside `app.asar`,
- whether the executable carries an embedded asar-integrity resource.

The patcher prints one identity line for the source build:

```text
Source <ProductName> version: <ProductVersion> (file <FileVersion>), <x64|arm64>, <signed|unsigned>, app.asar <sha256>
```

`ProductVersion` and `FileVersion` come from the executable's `RT_VERSION`
resource, the hash is the SHA-256 of the whole official `resources\app.asar`,
and the pair `(ProductVersion, FileVersion)` is the key of
`TESTED_WINDOWS_SOURCE_BUILDS`, the counterpart of the macOS
`(CFBundleShortVersionString, build)` table. The table is empty, so every run
needs `--allow-untested-source` and prints the untested-build warning.

## Shared with macOS

- **Renderer patch.** `patch_renderer` injects `ui/account-menu.js` and
  `ui/thread-subscription.js` into the same exact minified anchors, selecting
  the 7746 or 8109 table by the marker strings present in
  `webview/assets/app-initial-*.js` and `app-primary-*.js`. Renderer bundles
  are platform-independent JavaScript, so a Windows build of a supported
  version is expected to carry the same anchors; if it does not, the patch
  fails with the same "expected N … found M" errors as an unknown macOS build.
- **Main-process isolation.** `isolate_desktop_profile` rewrites the single
  `.vite/build/bootstrap-*.js`: exactly one `setPath('userData', …)` call
  becomes `appData + '/Codex Subscription Router'`, exactly one updater
  initialisation is removed, and `disable_updater_lifecycle` replaces the one
  `initializeUpdater()` body. The platform supplies only the environment
  prelude inserted before the rewrite. `install_ui_test_bridge` is shared.
- **The multiplexer.** `cmd/codex-mux` and `internal/*` are one code base.
  Routing, sticky ownership, failover, the control API on `127.0.0.1:48123`,
  the state layout under `~/.codex-mux`, and the account homes are identical.
  Only child termination, the shutdown signals, and the name of the parked
  real binary have per-platform files.
- **Tooling.** `@electron/asar` 4.3.0 is run through `node` and its ESM entry
  point `node_modules/@electron/asar/bin/asar.mjs` on every OS, never the
  `.cmd` shim, after `ensure_asar_tool` has checked the installed version
  against `package.json`. `asar_header_digest` records the header digest
  Electron validates, as on macOS since build 8109.
- **Token and backups.** `load_or_create_token` and the timestamped
  `~/.codex-mux/backups/<timestamp>/` move with rollback are shared.

## What differs

| Concern | macOS | Windows |
| --- | --- | --- |
| Destination | `~/Applications/Codex Subscription Router.app` | `%LOCALAPPDATA%\Programs\Codex Subscription Router\`, a full copy of the official install directory |
| Launcher | `native/launcher.c` compiled with `clang` into `Contents/MacOS/CodexSubscriptionRouterLauncher` | `cmd/codex-router-launcher` (Go) built as `Codex Subscription Router.exe` with `-trimpath -ldflags "-s -w -H=windowsgui -X main.electronExecutable=<name>"`; runs the sibling Electron executable with `--user-data-dir=%APPDATA%\Codex Subscription Router` first and its own arguments verbatim after, working directory its own, environment and stdio inherited, exit code passed through; any failure is shown in a `MessageBoxW` because `-H=windowsgui` hides the console |
| Bundled Codex | `Contents/Resources/codex` → mux, original kept as `codex.real` | the single `codex.exe` found under the copy → mux, original renamed `codex.real.exe` in the same directory; the mux looks for `codex.real.exe`, then `codex.real` |
| Asar integrity | `ElectronAsarIntegrity` in `Info.plist` | `INTEGRITY`/`ELECTRONASAR` resource in the Electron executable (what Electron's `archive_win.cc` reads), rewritten with `scripts/win/set-asar-integrity.mjs` (resedit, `ignoreCert: true`); rewriting drops the Authenticode signature |
| Signing | `codesign` under one Apple team, team continuity enforced | none; the copy runs unsigned and SmartScreen may warn |
| Computer Use | helper re-identified, re-signed, managed service pinned | not patched; `SKY_CUA_SERVICE_NATIVE_PIPE_PATH` set to `\\.\pipe\codex-subscription-router-computer-use`, a name the official app never uses, and `CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=1` |
| Desktop profile | `~/Library/Application Support/Codex Subscription Router` | `%APPDATA%\Codex Subscription Router` |
| State permissions | `0700` root, `0600` files | `icacls` at install time: inheritance removed, current user and SYSTEM only, inherited by later files; the mux's POSIX modes are no-ops beyond the read-only bit |
| URL scheme | `CFBundleURLSchemes` edited in `Info.plist` | the literal `'codex'` in `setAsDefaultProtocolClient`, `removeAsDefaultProtocolClient`, and `isDefaultProtocolClient` calls in `.vite/build/*.js` becomes `'codex-subscription-router'`; zero matches is a warning, not an error, because the layout is unverified |
| Child shutdown | `SIGINT` to each child | close the child's stdin (the app-server exits on EOF), wait up to 2 s, then `Kill`; `os.Process.Signal(os.Interrupt)` is unsupported on Windows |
| Mux shutdown signals | `SIGINT`, `SIGTERM` | the same list. Go's runtime delivers Ctrl-C and Ctrl-Break as `os.Interrupt` and CTRL_CLOSE, CTRL_LOGOFF and CTRL_SHUTDOWN console events as `SIGTERM`, so listing both keeps the deferred `multiplexer.Close()` running on logoff and shutdown; when the desktop app exits it closes stdin, which ends the mux on its own |
| Unpacked native modules | hard-coded `ASAR_UNPACK_DIRECTORIES` | derived from the official `resources\app.asar.unpacked\node_modules`; every path the official archive kept unpacked must still be unpacked after repacking |
| Source discovery | `/Applications/ChatGPT.app` | candidate list below; exactly one qualifying install; Microsoft Store/MSIX refused |
| Version identity | `CFBundleShortVersionString` and build from `Info.plist` | `ProductVersion` and `FileVersion` from `RT_VERSION` via `scripts/win/exe-info.mjs` |
| Path length | not a concern | projected longest path checked against `MAX_PATH` (260); `LongPathsEnabled=1` required beyond it |
| Installer | `install.sh` | `install.ps1` |
| Shortcut | Launch Services registration | Start menu `.lnk` via `WScript.Shell`; failure is a warning |

## Layout the patcher produces

```text
%LOCALAPPDATA%\Programs\Codex Subscription Router\
├── Codex Subscription Router.exe     launcher (Go, GUI subsystem, unsigned)
├── ChatGPT.exe                       copied Electron host; integrity resource
│                                     rewritten when present; unsigned
├── resources\app.asar                patched archive
├── resources\app.asar.unpacked\      native modules, same set as the official tree
└── <where the official build keeps it>\
    ├── codex.exe                     the multiplexer (cmd/codex-mux)
    └── codex.real.exe                the official binary, renamed
%APPDATA%\Codex Subscription Router\  Chromium / Electron profile
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Codex Subscription Router.lnk
%USERPROFILE%\.codex-mux\             state root (icacls-hardened); backups\<timestamp>\
%USERPROFILE%\.codex                  Primary account, owned by the official app
```

`ChatGPT.exe` is the default name; the patcher discovers the real one and
embeds it in the launcher at build time.

## Run-time discovery and checks

Every step stops the install on failure; the list is in execution order.

1. **Source.** `--source`, otherwise these candidates are examined:
   `%LOCALAPPDATA%\Programs\ChatGPT`, `%LOCALAPPDATA%\Programs\Codex`, the
   newest `%LOCALAPPDATA%\ChatGPT\app-*` and `%LOCALAPPDATA%\Codex\app-*`
   (Squirrel keeps the previous version beside the current one),
   `%ProgramFiles%\ChatGPT`, `%ProgramFiles%\Codex`. A candidate qualifies only
   if it is a directory containing `resources\app.asar`; exactly one must
   qualify, and the error lists what was examined. Any path containing a
   `WindowsApps` component is refused as a Store/MSIX install.
2. **Electron executable.** `--electron-executable NAME` (a bare file name),
   otherwise exactly one top-level `.exe` after excluding `uninstall*`,
   `unins*`, `update.exe`, `squirrel*.exe`, `elevate.exe`, and the launcher's
   own name.
3. **Executable facts.** `scripts/win/exe-info.mjs` parses the PE with
   `ignoreCert: true` and reports machine, subsystem, signature presence, the
   first `RT_VERSION` resource (first string table), and the
   `INTEGRITY`/`ELECTRONASAR` resource. A malformed resource, more than one
   language variant, or a non-`{file, alg, value}` list is an error, never
   reported as absent.
4. **Identity.** `(ProductVersion, FileVersion)` and the whole-file
   `app.asar` SHA-256 are compared with `TESTED_WINDOWS_SOURCE_BUILDS`; a
   mismatch stops unless `--allow-untested-source` is passed, which prints a
   warning and continues only while every later anchor matches.
5. **Tools.** `go`, `node`, and `npm` on `PATH`; `node_modules/@electron/asar`
   at the version `package.json` pins; a `node` new enough for it.
6. **State root.** `%USERPROFILE%\.codex-mux` is created with the control
   token, then hardened with `icacls`; an `icacls` failure stops the install.
7. **Destination.** Must not exist unless `--force`; with `--force`, no process
   may be running from it (`Get-CimInstance Win32_Process` executable-path
   prefix). Source and destination must differ and must not contain each
   other.
8. **Path length.** The longest path under the source, projected onto the
   staging directory (`destination.parent\.codex-subscription-router-XXXXXXXX\…`)
   plus an eight-character margin, must stay below 260 unless
   `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled` reads
   as `1`; an unreadable value counts as disabled.
9. **Build.** The source is copied to the staging directory; the mux is built
   with `GOOS=windows` and the launcher with the flags above. The Electron
   executable name may not contain whitespace or quotes, which `-X` cannot
   carry.
10. **Archive.** `asar list --is-pack` records the original packed/unpacked
    state; the archive is extracted; `isolate_desktop_profile` (Windows
    prelude), `install_ui_test_bridge`, `retarget_protocol_scheme` (zero
    matches warns), and `patch_renderer` run with their exact anchors.
11. **Repack.** `--unpack-dir` is derived from
    `resources\app.asar.unpacked\node_modules`: anything other than
    `node_modules` at the top level, no `node_modules`, or no packages is an
    error. After packing, every originally unpacked path must still be
    unpacked, and the unpacked tree must exist exactly when a pattern was
    given.
12. **Bundled Codex.** `--codex-executable RELPATH`, otherwise exactly one
    file named `codex.exe` anywhere under the copy. `codex.real.exe` must not
    already exist; the original is renamed and the mux copied into place.
13. **Integrity.** A `null` resource prints "no embedded asar integrity
    resource" and rewrites nothing. A present resource must contain an entry
    for `resources\app.asar` (compared case-insensitively with either
    separator) with algorithm `SHA256`; its value becomes
    `asar_header_digest` of the repacked archive, written atomically to a
    temp file that is verified before and after the rename, and re-checked by
    the patcher from the tool's output.
14. **Install.** An existing destination moves to
    `%USERPROFILE%\.codex-mux\backups\<YYYYmmdd-HHMMSS>\`; the staged copy is
    renamed into place; on failure the staged copy is parked under
    `failed-install` and the backup restored.
15. **Shortcut.** Best effort; a failure is a warning because the install is
    already launchable.

## Unverified assumptions

None of the following has been observed on an official Windows build. Each is
either checked at run time (and stops the install when wrong) or is marked as
not checkable.

Official app layout:

- The install lives at one of the candidate paths above and is not a Store/
  MSIX package. Checked; `--source` overrides.
- The Electron host is the single non-excluded top-level `.exe`, by default
  `ChatGPT.exe`, and the bundled `codex.exe` exists exactly once under the
  install (for example under `resources\app.asar.unpacked`). Checked; both
  have overrides.
- The Windows main-process bundles carry the macOS anchors: one
  `.vite/build/bootstrap-*.js` with the profile and updater patterns and one
  bundle with the `initializeUpdater` lifecycle. Windows builds usually update
  through Squirrel or electron-updater rather than Sparkle, so these anchors
  may differ or extra Windows-only update entry points (`Update.exe`,
  `--squirrel-*` arguments) may exist. The two anchors are checked; extra
  entry points are not detectable and must be reviewed by hand.
- Protocol registration uses `setAsDefaultProtocolClient` /
  `removeAsDefaultProtocolClient` / `isDefaultProtocolClient` with a literal
  `'codex'` in `.vite/build/*.js`. Zero matches only warns.
- The renderer bundles match a macOS table (7746 or 8109). Checked.
- `resources\app.asar.unpacked` contains only `node_modules` packages.
  Checked.
- The `INTEGRITY`/`ELECTRONASAR` resource, when present, is a JSON array of
  `{file, alg: "SHA256", value}` with a `resources\app.asar` entry, as
  `@electron/packager` writes it. Checked. Whether the embedded-asar-integrity
  fuse is enabled cannot be read by the tools; an enabled fuse with no
  resource would already prevent the official app from starting.
- The official executable's `RT_VERSION` has a string table (assumed
  language 1033) carrying `ProductVersion`, `FileVersion`, and `ProductName`.
  Missing values print as `unknown` and can never match the tested table.
- The Windows build honours `SKY_CUA_SERVICE_NATIVE_PIPE_PATH` and
  `CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH`; if it ignores them the
  prelude is harmless.
- The official bundled `codex.exe` is a console-subsystem PE like the mux
  build; if it were a GUI-subsystem binary, a console could flash when Electron
  spawns the mux.

Run-time behaviour:

- The Windows `codex.exe` app-server exits promptly on stdin EOF, as the
  macOS one does; hence the 2 s grace and kill backstop.
- `pe-library` can parse the real executable. It refuses PEs with a COFF
  symbol table and unusual resource layouts, and holds roughly three copies of
  the file in memory.
- `fs.renameSync` over the existing `.exe` succeeds (`MoveFileEx` with
  replace); it fails, leaving the target untouched, if the file is locked.
- Electron compares the stored `file` key against the archive path relative
  to the executable; the copy keeps the official relative layout, so the key
  is never rewritten.
- `appData + '/Codex Subscription Router'` (forward slash, set by the main
  process) and `--user-data-dir=%APPDATA%\Codex Subscription Router` (set by
  the launcher) resolve to the same directory.
- Chromium treats a later `--user-data-dir` in passthrough arguments as
  overriding the launcher's; the launcher passes arguments verbatim.
- Windows delivers console control events to the mux as `os.Interrupt`. When
  Electron spawns `codex.exe` without a console, no signal ever arrives and
  shutdown relies on the desktop app closing stdin.
- Neither the console-subsystem mux nor `codex.real.exe` flashes a console
  window; this depends on Electron's spawn flags and on the mux's child
  spawn, which sets no Windows-specific `SysProcAttr`.
- `icacls` resolves `USERDOMAIN\USERNAME`; Windows PowerShell 5.1 is on
  `PATH` as `powershell`; `Get-CimInstance Win32_Process` exposes
  `ExecutablePath` for the user's own processes.
- `winreg` can read `LongPathsEnabled`, and the Python interpreter is
  long-path aware (python.org builds are).
- The backup rename stays on one volume; a destination on another drive would
  take the rollback path.
- `GOARCH` is the host toolchain's default, not derived from the executable's
  machine type. An arm64 Windows host running an x64 official app gets an
  arm64 mux and launcher, which should run but is unexercised.
- SmartScreen and Defender behaviour towards the unsigned rewritten executable
  and the unsigned Go binaries is untested.
- Taskbar grouping: pins point at `Codex Subscription Router.exe` while the
  window belongs to the Electron executable; without a shared AppUserModelID
  Windows may show two taskbar entries.
- The launcher's `MessageBoxW`, exit-code propagation, `EvalSymlinks` on
  real install paths, and stdio inheritance were cross-compiled and vetted,
  not executed.

Installer and tooling:

- `install.ps1` was validated with PowerShell 7.4 on Linux; Windows PowerShell
  5.1 behaviour is inferred (no 7-only syntax, no embedded quotes in native
  arguments, no stderr redirection under `Stop`, `Get-Variable` guards for
  `$IsLinux`/`$IsMacOS`, ASCII-only file).
- `Get-Command -CommandType Application` resolves `npm.cmd`, `py.exe`, and
  `git.exe`; `& npm` propagates its exit code; the Microsoft Store `python.exe`
  placeholder exits non-zero so the fallback to `py -3` engages.
- `Stop-Process -Force` releases file locks within the ten-second window so
  `--force` can move the old copy.
- The `windows` CI job relies on `windows-latest` providing `python3` on
  `PATH` without `actions/setup-python` (this repository pins no such action),
  on Git Bash as `shell: bash`, and on the Python unit tests written on Linux
  passing under Windows path semantics.
- `asar list --is-pack` prints backslash paths on Windows (normalised) and
  `--unpack-dir` brace patterns match Windows relative paths. Checked by the
  unpacked-preserved comparison.

## Recording the first supported build

1. On a Windows PC with the official desktop app installed per user, clone
   the repository, run `npm ci --ignore-scripts`, and run
   `python scripts\patch_app_windows.py --allow-untested-source` (or
   `install.ps1` with `$env:CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE = '1'`).
2. If it stops, the error names the check that failed. Do not weaken the
   check; find out why the layout differs (next section) and add an exact,
   Windows-specific anchor with a test.
3. When it completes, keep the whole output. Capture the identity line
   (`ProductName`, `ProductVersion`, `FileVersion`, machine, signed state,
   whole-file `app.asar` SHA-256), `Multiplexer installed at <path>`, the
   protocol-retargeting count, and the integrity line.
4. Complete the Windows checklist in [SMOKE-TEST.md](SMOKE-TEST.md) in full.
   A build that launches but has not routed, failed over, and resumed threads
   across two accounts is not supported.
5. Add the build to `TESTED_WINDOWS_SOURCE_BUILDS` in
   `scripts/patch_app_windows.py` as
   `("<ProductVersion>", "<FileVersion>"): "<app.asar sha256>"`, using the
   whole-file digest from the identity line, not the header digest; extend the
   `approve_source` tests in `scripts/test_patch_app_windows.py` to accept it.
6. Add the row to the Windows table in [COMPATIBILITY.md](COMPATIBILITY.md),
   change its architecture row from "untested" to what was tested, update the
   Compatibility section of the README and the status in
   [MAINTAINED.md](MAINTAINED.md), and drop the `--allow-untested-source`
   requirement from the README's Windows instructions for that build.
7. Replace the provisional statements in this file with what was observed:
   the Electron executable name, the `codex.exe` path, whether the integrity
   resource existed, the unpacked package list, the protocol-retargeting
   count, and the Windows build number and tool versions.

## Re-deriving anchors for a Windows bundle

Work on a copy; never edit the official installation.

1. Extract the official archive:
   `node node_modules\@electron\asar\bin\asar.mjs extract "<install>\resources\app.asar" <dir>`.
2. For the main process, compare `.vite\build\bootstrap-*.js` and the other
   `.vite\build\*.js` bundles against a macOS extraction of the same version:
   the `setPath('userData', …)` call, the `initialize()` call before
   `runMainAppStartup`, the `initializeUpdater()` body, and the protocol
   registration calls. A Windows build may use a different updater; add its
   entry points as new exact anchors rather than relaxing the existing ones.
3. For the renderer, use the method in
   [BUILD-8109-PORT.md](BUILD-8109-PORT.md): tokenize both bundles into
   identifiers plus a punctuation skeleton, align within one function, and
   confirm every result with an exact occurrence count on the real bundle.
4. Put Windows-only anchors in `scripts/patch_app_windows.py`, keyed like the
   macOS tables, with unit tests; shared anchors stay shared.

## Checks

- Go: `go test ./...` covers the launcher's argument construction (default
  and `-X` override, passthrough order, error cases) and the real-executable
  lookup on Windows and macOS; `GOOS=windows GOARCH=amd64 go build ./...` and
  the arm64 equivalent must succeed.
- Node: `node --test "scripts/win/*.test.mjs"` (18 tests) cross-compiles a real
  PE32+ fixture with Go, adds packager-style `INTEGRITY` and `RT_VERSION`
  resources with resedit directly, and drives both CLIs through `spawnSync`,
  asserting exit codes and output; the fixture tests skip with a printed
  reason when `go` is not on `PATH`.
- Python: `npm run check:python` runs the shared byte-identity tests and 52
  Windows tests with `subprocess` mocked; the orchestration itself refuses to
  run off Windows.
- PowerShell: `install.ps1` is parsed with `Parser.ParseFile` in CI;
  `CODEX_SUBSCRIPTION_ROUTER_DRY_RUN=1` makes dot-sourcing it define the
  functions without installing, so `Assert-Prerequisite` and the source
  resolution can be exercised on any host.
- CI: the `macos` job adds the Windows cross-compile and the PE helper tests;
  the `windows` job repeats the Go, JavaScript, PE helper, Python, and release
  checks on `windows-latest` and parses `install.ps1`.

## Status

Implemented and unit-tested on Linux and in CI. Never launched against an
official Windows build: no Windows build is recorded in
`TESTED_WINDOWS_SOURCE_BUILDS` or [COMPATIBILITY.md](COMPATIBILITY.md), the
patcher requires `--allow-untested-source`, and the Windows smoke test has not
been run. Anchors matching and the build completing are not evidence the copy
runs; launch it, then route, fail over, and resume across two accounts.
