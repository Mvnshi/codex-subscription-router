# Porting notes: Windows

Status in one line: implemented and unit-tested on Linux, with CI repeating
the checks on macOS and Windows (the `macos` and `windows` jobs); never
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
hold exact expectations (bundle contents, error messages, and the
bootstrap-before-main patch order; the extracted tree is a
`TemporaryDirectory` discarded on failure) written against the pre-split code,
and they pass unchanged after it.
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
  Only child termination has per-platform files
  (`internal/backend/child_terminate_{unix,windows}.go`); the shutdown signal
  list is shared, and the parked real binary's name is a `GOOS` branch in the
  single `cmd/codex-mux/real_executable.go`.
- **Tooling.** `ensure_asar_tool` is shared: it checks that the installed
  `@electron/asar` matches the `4.3.0` pin in `package.json` and that `node` is
  new enough for it. How the CLI is then invoked differs: the Windows patcher
  runs `node node_modules/@electron/asar/bin/asar.mjs` directly, never the
  `.cmd` shim, while the macOS patcher keeps executing `node_modules/.bin/asar`
  exactly as before (on POSIX a symlink to that same `asar.mjs`).
  `asar_header_digest` records the header digest Electron validates, as on
  macOS since build 8109.
- **Token and backups.** `load_or_create_token` and the timestamped
  `~/.codex-mux/backups/<timestamp>/` backup location are shared; the move
  itself and its rollback are per platform (`swap_into_place` on Windows,
  step 15 below).

## What differs

| Concern | macOS | Windows |
| --- | --- | --- |
| Destination | `~/Applications/Codex Subscription Router.app` | `%LOCALAPPDATA%\Programs\Codex Subscription Router\`, a full copy of the official install directory |
| Launcher | `native/launcher.c` compiled with `clang` into `Contents/MacOS/CodexSubscriptionRouterLauncher` | `cmd/codex-router-launcher` (Go) built as `Codex Subscription Router.exe` with `-trimpath -ldflags "-s -w -H=windowsgui -X main.electronExecutable=<name>"`; runs the sibling Electron executable with `--user-data-dir=%APPDATA%\Codex Subscription Router` first and its own arguments verbatim after, working directory its own, environment and stdio inherited, exit code passed through; any failure is shown in a `MessageBoxW` because `-H=windowsgui` hides the console |
| Bundled Codex | `Contents/Resources/codex` → mux, original kept as `codex.real` | the single `codex.exe` found under the copy → mux, original renamed `codex.real.exe` in the same directory; the mux looks for `codex.real.exe`, then `codex.real`. When `codex.exe` lives inside `app.asar.unpacked`, the repacked archive header keeps the official binary's recorded size and SHA-256 for that path and lists no `codex.real.exe`; harmless because Electron reads unpacked entries from disk without checking them, but the header is not a description of the installed binary |
| Asar integrity | `ElectronAsarIntegrity` in `Info.plist` | `INTEGRITY`/`ELECTRONASAR` resource in the Electron executable (what Electron's `archive_win.cc` reads), rewritten with `scripts/win/set-asar-integrity.mjs` (resedit, `ignoreCert: true`); rewriting drops the Authenticode signature |
| Signing | `codesign` under one Apple team, team continuity enforced | none; the copy runs unsigned and SmartScreen may warn |
| Computer Use | helper re-identified, re-signed, managed service pinned | not patched; `SKY_CUA_SERVICE_NATIVE_PIPE_PATH` set to the prefix `\\.\pipe\codex-subscription-router-computer-use-` plus a `globalThis.crypto.randomUUID()` drawn on every launch (the Windows pipe namespace is machine-global, so a fixed name could be pre-created by any other local account and answered as a fake helper; the macOS socket gets that protection from the `0700` state root), and `CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=1` |
| Desktop profile | `~/Library/Application Support/Codex Subscription Router` | `%APPDATA%\Codex Subscription Router` |
| State permissions | `0700` root, `0600` files | `icacls` at install time: explicit ACEs on the root reset first, then inheritance removed, current user and SYSTEM only, inherited by files and directories created under the root later (a previous install renamed into `backups\` keeps its own ACL); the mux's POSIX modes are no-ops beyond the read-only bit |
| URL scheme | `CFBundleURLSchemes` edited in `Info.plist` | the literal `'codex'` in `setAsDefaultProtocolClient`, `removeAsDefaultProtocolClient`, and `isDefaultProtocolClient` calls in `.vite/build/*.js` becomes `'codex-subscription-router'`; a `setAsDefaultProtocolClient` call whose scheme is not the literal `'codex'` (a variable, another literal, `.call`/`.apply`, an alias) stops the patch before the bundle is written, because the copy would register that scheme for itself under `HKCU\Software\Classes`; only the total absence of any such call is a warning |
| Child shutdown | `SIGINT` to each child | close the child's stdin (the app-server exits on EOF), wait up to 2 s, then `Kill`; `os.Process.Signal(os.Interrupt)` is unsupported on Windows |
| Mux shutdown signals | `SIGINT`, `SIGTERM` | the same list. Go's runtime delivers Ctrl-C and Ctrl-Break as `os.Interrupt` and CTRL_CLOSE, CTRL_LOGOFF and CTRL_SHUTDOWN console events as `SIGTERM`, so listing both keeps the deferred `multiplexer.Close()` running on logoff and shutdown; when the desktop app exits it closes stdin, which ends the mux on its own |
| Unpacked native modules | hard-coded `ASAR_UNPACK_DIRECTORIES` | derived from the official `resources\app.asar.unpacked\node_modules`; every path the official archive kept unpacked must still be unpacked after repacking |
| Source discovery | `/Applications/ChatGPT.app` | candidate list below; exactly one qualifying install; Microsoft Store/MSIX refused |
| Version identity | `CFBundleShortVersionString` and build from `Info.plist` | `ProductVersion` and `FileVersion` from `RT_VERSION` via `scripts/win/exe-info.mjs` |
| Asar CLI invocation | `node_modules/.bin/asar` (POSIX symlink to `asar.mjs`) | `node node_modules\@electron\asar\bin\asar.mjs`; the `.cmd` shim is never used |
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
   `WindowsApps` component is refused as a Store/MSIX install. Source and
   destination must differ and must not contain each other.
2. **Electron executable.** `--electron-executable NAME` (a bare file name),
   otherwise exactly one top-level `.exe` after excluding `uninstall*`,
   `unins*`, `update.exe`, `squirrel*.exe`, `elevate.exe`, and the launcher's
   own name.
3. **Source layout.** Everything that depends only on the source is checked
   before any tool runs and before anything is copied or built, so an
   unknown layout stops the run in seconds: the Electron executable name may
   not contain whitespace or quotes, which the launcher's `-X` flag cannot
   carry; the `--unpack-dir` pattern is derived from
   `resources\app.asar.unpacked\node_modules` (anything other than
   `node_modules` at the top level, no `node_modules`, or no packages is an
   error); the bundled Codex is `--codex-executable RELPATH`, otherwise
   exactly one file named `codex.exe` anywhere under the source; and
   `codex.real.exe` must not already exist beside it.
4. **Tools.** `go`, `node`, and `npm` on `PATH`; `node_modules/@electron/asar`
   at the version `package.json` pins; a `node` new enough for it.
5. **Executable facts.** `scripts/win/exe-info.mjs` parses the PE with
   `ignoreCert: true` and reports machine, subsystem, signature presence, the
   first `RT_VERSION` resource (first string table), and the
   `INTEGRITY`/`ELECTRONASAR` resource. A malformed resource, more than one
   language variant, or a non-`{file, alg, value}` list is an error, never
   reported as absent.
6. **Identity.** `(ProductVersion, FileVersion)` and the whole-file
   `app.asar` SHA-256 are compared with `TESTED_WINDOWS_SOURCE_BUILDS`; a
   mismatch stops unless `--allow-untested-source` is passed, which prints a
   warning and continues only while every later anchor matches.
7. **State root.** `%USERPROFILE%\.codex-mux` is created with the control
   token, then hardened with `icacls` (explicit ACEs on the root reset, then
   inheritance removed and only the current user and SYSTEM granted); an
   `icacls` failure stops the install.
8. **Destination.** Must not exist unless `--force`; with `--force`, no process
   may be running from it (`Get-CimInstance Win32_Process` executable-path
   prefix).
9. **Path length.** The longest path under the source, projected onto the
   staging directory (`destination.parent\.codex-subscription-router-XXXXXXXX\…`)
   plus an eight-character margin, must stay below 260 unless
   `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled` reads
   as `1`; an unreadable value counts as disabled.
10. **Build.** The source is copied into the staging directory, a
    `TemporaryDirectory` beside the destination that is discarded with
    everything in it when any later step fails; the mux is built with
    `GOOS=windows` and the launcher with the flags above.
11. **Archive.** `asar list --is-pack` records the original packed/unpacked
    state; the archive is extracted; `isolate_desktop_profile` (Windows
    prelude), `install_ui_test_bridge`, `retarget_protocol_scheme`, and
    `patch_renderer` run with their exact anchors. `retarget_protocol_scheme`
    fails closed: a `setAsDefaultProtocolClient` call whose scheme is not the
    literal `'codex'` stops the patch before the bundle is written; only the
    total absence of any such call warns.
12. **Repack.** The archive is packed with the `--unpack-dir` pattern from
    step 3. Afterwards every originally unpacked path must still be unpacked,
    and the unpacked tree must exist exactly when a pattern was given.
13. **Bundled Codex.** The `codex.exe` located in step 3 is renamed
    `codex.real.exe` on the staged copy (its absence is re-checked there) and
    the mux is copied into place.
14. **Integrity.** A `null` resource prints "no embedded asar integrity
    resource" and rewrites nothing. A present resource must contain an entry
    for `resources\app.asar` (compared case-insensitively with either
    separator) with algorithm `SHA256`; its value becomes
    `asar_header_digest` of the repacked archive, written atomically to a
    temp file that is verified before and after the rename, and re-checked by
    the patcher from the tool's output.
15. **Install.** An existing destination is moved to
    `%USERPROFILE%\.codex-mux\backups\<YYYYmmdd-HHMMSS>\` (a rename on the
    same volume; across volumes it falls back to a copying move), then the
    staged copy is renamed into place with a single same-volume rename. If
    either move fails, the previous install is left where it is or moved back
    from the backup, and the staged copy is discarded with the temporary
    directory; nothing is parked under a `failed-install` directory any more.
16. **Shortcut.** Best effort; a failure is a warning because the install is
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
- When `codex.exe` lives inside `app.asar.unpacked`, the repacked archive
  header keeps the official binary's recorded size and SHA-256 for that path
  and lists no `codex.real.exe`; harmless because Electron reads unpacked
  entries from disk without checking them, but the header is not a
  description of the installed binary. Not checkable by the patcher.
- The Windows main-process bundles carry the macOS anchors: one
  `.vite/build/bootstrap-*.js` with the profile and updater patterns and one
  bundle with the `initializeUpdater` lifecycle. Windows builds usually update
  through Squirrel or electron-updater rather than Sparkle, so these anchors
  may differ or extra Windows-only update entry points (`Update.exe`,
  `--squirrel-*` arguments) may exist. The two anchors are checked; extra
  entry points are not detectable and must be reviewed by hand.
- Protocol registration uses `setAsDefaultProtocolClient` /
  `removeAsDefaultProtocolClient` / `isDefaultProtocolClient` with a literal
  `'codex'` in `.vite/build/*.js`. Checked: a `setAsDefaultProtocolClient`
  call with any other scheme expression stops the patch; only the total
  absence of such a call warns.
- The renderer bundles match a macOS table (7746 or 8109). Checked.
- `resources\app.asar.unpacked` contains only `node_modules` packages.
  Checked.
- The `INTEGRITY`/`ELECTRONASAR` resource, when present, is a JSON array of
  `{file, alg: "SHA256", value}` with a `resources\app.asar` entry, as
  `@electron/packager` writes it. Checked. Whether the embedded-asar-integrity
  fuse is enabled cannot be read by the tools; an enabled fuse with no
  resource would already prevent the official app from starting.
- The official executable's `RT_VERSION` has a string table (the first one
  present, whatever its language) carrying `ProductVersion`, `FileVersion`,
  and `ProductName`. Missing values print as `unknown` and can never match the
  tested table.
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
  symbol table and unusual resource layouts. One parse holds the file buffer
  plus pe-library's own copies of the sections; `set-asar-integrity` parses
  the input, the temp file, and the final file in turn, so budget several
  times the executable's size for it.
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
- Go delivers Ctrl-C and Ctrl-Break to the mux as `os.Interrupt` and the
  CTRL_CLOSE, CTRL_LOGOFF and CTRL_SHUTDOWN console events as `SIGTERM`. When
  Electron spawns `codex.exe` without a console, no console event ever arrives
  and shutdown relies on the desktop app closing stdin.
- Neither the console-subsystem mux nor `codex.real.exe` flashes a console
  window; this depends on Electron's spawn flags and on the mux's child
  spawn, which sets no Windows-specific `SysProcAttr`.
- `icacls` resolves `USERDOMAIN\USERNAME`; Windows PowerShell 5.1 is on
  `PATH` as `powershell`; `Get-CimInstance Win32_Process` exposes
  `ExecutablePath` for the user's own processes.
- `winreg` can read `LongPathsEnabled`, and the Python interpreter is
  long-path aware (python.org builds are).
- The backup move is a rename when `%USERPROFILE%\.codex-mux` and the
  destination share a volume; across volumes (a `--destination` on another
  drive, a relocated profile) it falls back to a copying move, which is not
  atomic. The staged copy's final rename never crosses volumes because the
  staging directory is created beside the destination.
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
  arguments, `Get-Variable` guards for `$IsLinux`/`$IsMacOS`, ASCII-only
  file). `Invoke-NativeCommand` and `Get-NativeOutput` run every native
  command with a function-local `$ErrorActionPreference = 'Continue'` and no
  stderr redirection, so 5.1 cannot turn a stderr line into a terminating
  error; they clear `$global:LASTEXITCODE` before each call and fail closed
  when it is still null afterwards (the command never ran).
- `Get-Command -CommandType Application` resolves `npm.cmd`, `py.exe`, and
  `git.exe`; npm is run through that resolved `npm.cmd` path, because a bare
  `npm` would resolve to `npm.ps1`, which Windows PowerShell 5.1's default
  execution policy refuses; the Microsoft Store `python.exe` placeholder exits
  non-zero so the fallback to `py -3` engages.
- `Stop-Process -Force` releases file locks within the ten-second window so
  `--force` can move the old copy.
- The `windows` CI job relies on `windows-latest` providing `python` on `PATH`
  without `actions/setup-python` (this repository pins no such action); the
  image does not reliably expose `python3`, so the job invokes `python`
  directly instead of the npm `check:python` and `release:check` scripts. It
  also relies on Git Bash as `shell: bash`, and on the Python unit tests
  written on Linux passing under Windows path semantics.
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
- Node: `node --test "scripts/win/*.test.mjs"` cross-compiles a real PE32+
  fixture with Go, adds packager-style `INTEGRITY` and `RT_VERSION` resources
  with resedit directly, and drives both CLIs through `spawnSync`, asserting
  exit codes, stdout/stderr contents, and that a refused rewrite leaves the
  input untouched; the fixture tests skip with a printed reason when `go` is
  not on `PATH`, while the pure-helper and usage-error tests always run.
- Python: `npm run check:python` runs the shared byte-identity tests and the
  Windows tests, which cover source and executable discovery, the exact
  anchors and the prelude, the `icacls`, PowerShell, and Go command
  construction, helper output decoding, the integrity rewrite, the
  backup/restore swap, and the orchestration order with `subprocess` and the
  helpers mocked; the orchestration itself refuses to run off Windows.
- PowerShell: `install.ps1` is parsed with `Parser.ParseFile` in CI;
  `CODEX_SUBSCRIPTION_ROUTER_DRY_RUN=1` makes dot-sourcing it define the
  functions without installing. On a non-Windows host `Assert-Prerequisite`
  then fails at its Windows-host check, so only the helpers that do not need
  Windows (version parsing, path normalisation) can be exercised there.
- CI: the `macos` job adds the Windows cross-compile and the PE helper tests;
  the `windows` job repeats the Go, JavaScript, PE helper, Python, and release
  checks on `windows-latest` and parses `install.ps1`.

## Status

Implemented and unit-tested on Linux; CI repeats the checks on macOS and
Windows (the `macos` and `windows` jobs). Never launched against an
official Windows build: no Windows build is recorded in
`TESTED_WINDOWS_SOURCE_BUILDS` or [COMPATIBILITY.md](COMPATIBILITY.md), the
patcher requires `--allow-untested-source`, and the Windows smoke test has not
been run. Anchors matching and the build completing are not evidence the copy
runs; launch it, then route, fail over, and resume across two accounts.
