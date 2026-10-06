# Porting notes: Windows

Status in one line: provisional. Verified on one real build, the Microsoft
Store package `OpenAI.Codex` `26.930.3930.0` (app build `12947`): it patches,
launches beside the official app, runs the multiplexer, connects accounts, and
routes, switches and moves chats between two real subscriptions; its usage
sheet with per-account resets and its per-account plugin connections work too
(checked live, see [COMPATIBILITY.md](COMPATIBILITY.md)). Computer Use is off in
OpenAI's Windows build unless the environment turns it on, and the router leaves
that alone. ARM64 builds natively and is tested in CI, but the app has not run on
ARM64 hardware. The first version of this port (written without a Windows build
to test against) was wrong about the one thing that matters most; see
[Observed on the Store build](#observed-on-the-store-build).

Every other statement below about the official Windows app is still a run-time
check the patcher performs rather than an observation, unless the section says
it was observed. Nothing here weakens an anchor: where the Windows layout
differs from what is expected, the patcher stops and names what it found.

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
`(CFBundleShortVersionString, build)` table. On the Store build the host is
Chromium's launcher, so the pair is the Chromium runtime version and the
`app.asar` hash is the real identity; a key may therefore list several hashes.
The table holds one provisional entry (the Store build under
[Observed on the Store build](#observed-on-the-store-build)); every other source
needs `--allow-untested-source` and prints the untested-build warning.

## Observed on the Store build

Measured on `OpenAI.Codex` `26.930.3930.0` (x64) under Windows 11 Pro
10.0.26100, with Go 1.27.0, Node.js 24.17.0 and Python 3.12.10.

**Distribution.** OpenAI ships the Windows desktop app only as a Microsoft Store
(MSIX) package. The original design assumed a per-user installer and refused
every `WindowsApps` path, on the stated belief that a copy "would neither read
completely nor launch". Both halves were wrong: the owning user can read every
file in the package (4,156 entries, 2.1 GB, no read-only files), the package's
`app` directory copies to an ordinary folder, and the copy launches. A standard
user cannot enumerate `%ProgramFiles%\WindowsApps` itself, so discovery asks the
package registry (`Get-AppxPackage -Name OpenAI.Codex`, no elevation) for the
install location and takes its `app` subdirectory.

**Layout.**

- The host is Chromium's launcher: `ChatGPT.exe` (4.7 MB, `OriginalFilename`
  `chrome.exe`, Chromium `154.0.8037.98`) loads a 327 MB `chrome.dll`, with
  `resources\owl-app.ini` and `owl-electron-app.json` beside the archive. It is
  one of seven top-level executables (`chrome_proxy.exe`, `elevation_service.exe`,
  ...), so the host is taken from the package manifest: the first `Application`'s
  `Executable` in `AppxManifest.xml` (`app/ChatGPT.exe`).
- `ChatGPT.exe` carries a standard `INTEGRITY` resource (`resources\app.asar`,
  `SHA256`), which the existing resedit path rewrites. A search of `chrome.dll`,
  `ChatGPT.exe` and `chrome_elf.dll` found no embedded-digest sentinel like the
  one the macOS 12246 port had to patch, and the rewritten copy starts.
- `resources\codex.exe` is the engine (326 MB); a 20 KB `Codex.exe` stub sits
  beside the host and also matches a case-insensitive search for `codex.exe`, so
  discovery prefers the match under `resources` when it leaves exactly one.
  `resources\codex` (289 MB, no extension) is the Linux engine used for WSL mode
  and is not patched: sessions run inside WSL bypass routing.
- `app.asar` is 549 MB with 21,032 entries; the official archive keeps 48
  entries unpacked: whole `build`/`lib` directories of `better-sqlite3` and
  `node-pty`, and two loose `.node` files in a nested `@worklouder` package.

**How the app launches the engine.** In the Store package the app copies
`resources\codex.exe` and its helper executables to
`%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\` and runs that copy, but only when the
resources path contains `WindowsApps` (`bundled_executable_relocation`). In the
copy the path is an ordinary folder, so the app runs `resources\codex.exe`
directly: the multiplexer, with `codex.real.exe` beside it, exactly as designed.
Observed: the host starts `resources\codex.exe ... app-server` (the multiplexer,
listening on `127.0.0.1:48123`), which runs one `codex.real.exe app-server` per
connected account (two, once a second subscription was added). The host also
starts a separate `codex.exe exec-server --remote <cloud environments URL>`,
which the multiplexer hands to its own `codex.real.exe`; a process count taken
from the checklist's "one `codex.real.exe` per enabled account" therefore reads
one higher than the account count.

**Anchors.** On this build the shared patch steps matched except two, both fixed
without loosening what they check:

- *Usage sheet selection propagation* hardcoded `t` for the React compiler's
  memo-cache array; this build's minifier named it `n`. The anchor now captures
  the identifier and requires the same one in every later use.
- *Protocol registration*: the build's own `registerProtocolClient` returns on
  `win32` before it reaches `setAsDefaultProtocolClient`, because the manifest
  declares `codex://`. The residue check refused the call (correctly, since it
  could not tell it never runs). It now accepts that call only inside the exact
  shape `if(process.platform===\`win32\`)return;let t=f(e.isPackaged);try{e.setAsDefaultProtocolClient(t)`.
  Platform guard missing, wrong, inverted, not returning, or any statement between
  guard and call: still stops the patch. After a build the copy had registered no
  protocol handler, and the official app's Chrome native-messaging registration
  and manifest were unchanged.

**Unpacking.** The first design repacked with `--unpack-dir node_modules/{<every
top-level package>}`. That also unpacked every packed file under `@worklouder`,
including a nested `...\parser-delimiter\dist` directory whose path passed the
248-character directory limit, so the install failed with `WinError 206` after the
copy. The patch now derives its options from the official archive's own
`asar list --is-pack`: top-most unpacked directories become `--unpack-dir`
patterns, remaining unpacked files become `--unpack` patterns by base name (a path
pattern cannot cross the dot-directory the staging copy lives in, because
minimatch's `**` skips dot-directories), and the repacked listing must then match
the official unpacked set exactly, not merely contain it.

**Paths.** The source's deepest path is 246 characters (168 below `app`), which a
default Windows configuration can only handle if the staged copy sits under a
short prefix. The staging directory is `<Programs>\.csr-XXXXXXXX\app` (it was
`.codex-subscription-router-XXXXXXXX\Codex Subscription Router`, 44 characters
longer); with that, a default install (`LongPathsEnabled` off) works and the
check still stops the run when a very long profile path would not fit.

**What the copy does not have.** The Store manifest declares things at install
time that a plain copy cannot have: the `codex://` handler, an Explorer
context-menu COM server, inbound firewall rules for the app on ports 1455 and
1457, the `CodexSandboxService.OpenAI.Codex` Windows service, and package
identity. The copy therefore leaves `codex://` and the Explorer menu with the
official app, signs in through the device-code flow (which needs no local
callback port), and runs the engine without the Store-managed sandbox service;
how the engine sandboxes commands in that case has not been tested.

**Side effects checked.** Building and running the copy left the official
package's `app.asar` hash (`af98213984ec...abba`) and Authenticode signature
unchanged, left no `codex-subscription-router://` handler, and left the Chrome
native-messaging registration for `com.openai.codexextension` pointing at the
official manifest, which was not modified.

## Routing, failover and lifecycle on the Store build

**Live, on real accounts.** A headless client (the same handshake as the desktop
app, including `capabilities.experimentalApi`) drove the router's real
multiplexer over stdio against a Pro 5x account whose weekly limit was reached and
a fresh Plus account, using throwaway chats pinned to the built-in `openai`
provider and archived afterwards. All 12 checks passed: a new chat on Automatic
went to the account with usage; a real turn ran there and answered; the chat
stayed on its account; moving it to the other account did a real history transfer
(the rollout file appeared under that account's `sessions`); a follow-up in a chat
owned by the spent account was moved back to the account with usage before it was
sent, answered with the earlier context intact, and showed the client no
usage-limit error; pinning the spent account fell back for a new chat; pinning an
account with usage was honoured.

One thing the first attempt exposed is worth knowing: `thread/resume` with a
`path` is an experimental call, and the engine rejects it with
`thread/resume.path requires experimentalApi capability` unless the client
declared `experimentalApi` at `initialize`. The desktop app does (its bootstrap
sets it), and the multiplexer forwards the client's handshake to every engine, so
the router is unaffected; a different client driving the multiplexer has to
declare it too.

**Automated.** `internal/mux/failover_e2e_test.go` runs the real multiplexer
against two real child processes (a scripted engine shaped like the newer one) and
the real filesystem, on every operating system in CI. It covers where new chats
go, a pin on a spent account, a turn moved before it is sent, a turn killed by a
usage-limit notification and replayed, a `turn/start` rejected with a usage limit
and replayed, every account spent, and follow-ups staying on the owning account.
Four deliberate breakages of the router (ignoring usage-limit notifications,
forgetting the new owner after a move, skipping the capacity check, moving a chat
without its history) were each caught by these tests.

**Not covered live.** The reactive paths (a turn the engine itself rejects or
kills because of a usage limit) were not driven against the real engine: the
router's own usage check moves a chat first, and the rate-limit preview can only
make an account look spent, never healthy. They are covered by the automated tests.

**Two apps, one chat store.** The copy and the official app share `~/.codex`, so
both list the same chats, but a turn runs on the engine of the app it was sent
from: the engine log shows a message sent in the official app handled by the
official app's engine (under `%LOCALAPPDATA%\OpenAI\Codex\bin`) while the
router's engines did nothing for it. Only the router's window routes between
subscriptions. A long-running chat that is loaded in the official app (a "goal"
that keeps retrying, say) stays on that engine and is not moved by the router.

**Providers and gateways.** Per-account homes inherit the primary account's
`config.toml`, including `model_provider`. By its code, one local gateway that was
read forwards each engine's own login to the plan endpoint (so accounts stay
separate) and switches to a paid third-party provider when the plan answers a
quota error; this was read from the gateway's source, not observed on the wire.
With such a gateway in the path the router never sees that quota error, so it
cannot fail a chat over mid-turn; its proactive moves (new chats, and turns on a
chat whose owner's usage shows it spent) are unaffected because they use the
account usage read, not the model traffic. When every subscription is spent the
router answers a new chat with "All connected subscriptions are depleted" instead
of letting the gateway serve it.

**Engine provider override.** `CODEX_MUX_ENGINE_PROVIDER`, or the first line of
`<state root>/engine-provider`, makes the multiplexer start every engine with
`-c model_provider=<id>` ahead of the desktop app's own arguments. Observed with
`openai` on the Store build: both of the router's engines ran with that flag, a
chat started without naming a provider reported `openai`, a real turn completed,
and the gateway's fallback counter did not move; the official app's engines were
unaffected. One `GET /models` at engine start still went to the provider from the
config. The id becomes part of a command line, so only plain ids
(`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`) are accepted, and an invalid value stops the
multiplexer with a message naming its source.

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
| Destination | `~/Applications/Codex Subscription Router.app` | `%LOCALAPPDATA%\Programs\Codex Subscription Router\`, a full copy of the official install directory (for the Store package, its `app` subdirectory) |
| Launcher | `native/launcher.c` compiled with `clang` into `Contents/MacOS/CodexSubscriptionRouterLauncher` | `cmd/codex-router-launcher` (Go) built as `Codex Subscription Router.exe` with `-trimpath -ldflags "-s -w -H=windowsgui -X main.electronExecutable=<name>"`; runs the sibling Electron executable with `--user-data-dir=%APPDATA%\Codex Subscription Router` first and its own arguments verbatim after, working directory its own, environment and stdio inherited, exit code passed through; any failure is shown in a `MessageBoxW` because `-H=windowsgui` hides the console |
| Bundled Codex | `Contents/Resources/codex` → mux, original kept as `codex.real` | the single `codex.exe` found under the copy (preferring the one under `resources` when a same-named stub sits beside the host) → mux, original renamed `codex.real.exe` in the same directory; the mux looks for `codex.real.exe`, then `codex.real`. When `codex.exe` lives inside `app.asar.unpacked`, the repacked archive header keeps the official binary's recorded size and SHA-256 for that path and lists no `codex.real.exe`; harmless because Electron reads unpacked entries from disk without checking them, but the header is not a description of the installed binary |
| Asar integrity | `ElectronAsarIntegrity` in `Info.plist` | `INTEGRITY`/`ELECTRONASAR` resource in the Electron executable (what Electron's `archive_win.cc` reads), rewritten with `scripts/win/set-asar-integrity.mjs` (resedit, `ignoreCert: true`); rewriting drops the Authenticode signature |
| Signing | `codesign` under one Apple team, team continuity enforced | none; the copy runs unsigned and SmartScreen may warn |
| Computer Use | helper re-identified, re-signed, managed service pinned | not patched, and nothing is set for it: the Windows client of `@oai/sky` starts its helper as a child process over stdio, so there is no service or socket to pin, and the two macOS-only variables the macOS prelude sets (`SKY_CUA_SERVICE_NATIVE_PIPE_PATH`, `CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH`) are only read on `darwin` in the Store build. The feature is off unless `CODEX_ELECTRON_ENABLE_WINDOWS_COMPUTER_USE=1` is set in the environment, which the copy inherits unchanged |
| Desktop profile | `~/Library/Application Support/Codex Subscription Router` | `%APPDATA%\Codex Subscription Router` |
| State permissions | `0700` root, `0600` files | `icacls` at install time: explicit ACEs on the root reset first, then inheritance removed, current user and SYSTEM only, inherited by files and directories created under the root later (a previous install renamed into `backups\` keeps its own ACL); the mux's POSIX modes are no-ops beyond the read-only bit |
| URL scheme | `CFBundleURLSchemes` edited in `Info.plist` | the literal `'codex'` in `setAsDefaultProtocolClient`, `removeAsDefaultProtocolClient`, and `isDefaultProtocolClient` calls in `.vite/build/*.js` becomes `'codex-subscription-router'`; a `setAsDefaultProtocolClient` call whose scheme is not the literal `'codex'` (a variable, another literal, `.call`/`.apply`, an alias) stops the patch before the bundle is written, because the copy would register that scheme for itself under `HKCU\Software\Classes`; only the total absence of any such call is a warning. The one exception is a call inside the exact win32-guarded early-return shape the Store build uses, which never runs on Windows (see [Observed on the Store build](#observed-on-the-store-build)) |
| Child shutdown | `SIGINT` to each child | close the child's stdin (the app-server exits on EOF), wait up to 2 s, then `Kill`; `os.Process.Signal(os.Interrupt)` is unsupported on Windows |
| Mux shutdown signals | `SIGINT`, `SIGTERM` | the same list. Go's runtime delivers Ctrl-C and Ctrl-Break as `os.Interrupt` and CTRL_CLOSE, CTRL_LOGOFF and CTRL_SHUTDOWN console events as `SIGTERM`, so listing both keeps the deferred `multiplexer.Close()` running on logoff and shutdown; when the desktop app exits it closes stdin, which ends the mux on its own |
| Unpacked native modules | hard-coded `ASAR_UNPACK_DIRECTORIES` | derived from the official archive's own `asar list --is-pack` (top-most unpacked directories as `--unpack-dir`, other unpacked files as `--unpack` by base name); the repacked archive must keep exactly the official unpacked set, no more and no less |
| Source discovery | `/Applications/ChatGPT.app` | the newest installed Microsoft Store package `OpenAI.Codex` (via the package registry) plus the candidate list below; exactly one qualifying install |
| Version identity | `CFBundleShortVersionString` and build from `Info.plist` | `ProductVersion` and `FileVersion` from `RT_VERSION` via `scripts/win/exe-info.mjs` |
| Asar CLI invocation | `node_modules/.bin/asar` (POSIX symlink to `asar.mjs`) | `node node_modules\@electron\asar\bin\asar.mjs`; the `.cmd` shim is never used |
| Path length | not a concern | the copy is staged under `<Programs>\.csr-XXXXXXXX\app` (short on purpose); the projected longest path is checked against `MAX_PATH` (260) and `LongPathsEnabled=1` is required only beyond it |
| Installer | `install.sh` | `install.ps1` |
| Launch | Launch Services registration; the app keeps its bundle name | Start menu and Desktop `.lnk` named **Codex Router** via `WScript.Shell`, with the router's own icon; the old name is removed when it points at this launcher; failure is a warning |
| Icons | the official ones | the window and tray icons are plain files beside `app.asar` (`resources\chatgpt-app-{dark,light}.ico`, `chatgpt-tray-{dark,light}.ico`, looked up by name from `process.resourcesPath`); the copy gets the router's own (`assets/windows`, drawn by `scripts/make_icons.py` from the website mark) under those names, so the taskbar and notification area tell the two apps apart |

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
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Codex Router.lnk  and  Desktop\Codex Router.lnk
<destination>\Codex Router.ico       the shortcuts' icon; the window and tray icons are
                                      replaced in resources\ with the router's own
%USERPROFILE%\.codex-mux\             state root (icacls-hardened); backups\<timestamp>\
%USERPROFILE%\.codex                  Primary account, owned by the official app
```

`ChatGPT.exe` is the default name; the patcher discovers the real one and
embeds it in the launcher at build time.

## Run-time discovery and checks

Every step stops the install on failure; the list is in execution order.

1. **Source.** `--source`, otherwise these candidates are examined: the
   `app` directory of the newest installed Microsoft Store package
   `OpenAI.Codex` (asked of the package registry; a failed lookup means no Store
   candidate), `%LOCALAPPDATA%\Programs\ChatGPT`,
   `%LOCALAPPDATA%\Programs\Codex`, the newest `%LOCALAPPDATA%\ChatGPT\app-*`
   and `%LOCALAPPDATA%\Codex\app-*` (Squirrel keeps the previous version beside
   the current one), `%ProgramFiles%\ChatGPT`, `%ProgramFiles%\Codex`. A
   candidate qualifies only if it is a directory containing
   `resources\app.asar`; exactly one must qualify, and the error lists what was
   examined (and, when nothing qualifies, says to install the app from the
   Microsoft Store). A Store source is only ever read. Source and destination
   must differ and must not contain each other.
2. **Electron executable.** `--electron-executable NAME` (a bare file name);
   otherwise, for a Store package, the executable its `AppxManifest.xml` names
   for the first `Application` when that file sits directly in the `app`
   directory; otherwise exactly one top-level `.exe` after excluding
   `uninstall*`, `unins*`, `update.exe`, `squirrel*.exe`, `elevate.exe`, and the
   launcher's own name.
3. **Source layout.** Everything that depends only on the source is checked
   before any tool runs and before anything is copied or built, so an
   unknown layout stops the run in seconds: the Electron executable name may
   not contain whitespace or quotes, which the launcher's `-X` flag cannot
   carry; the bundled Codex is `--codex-executable RELPATH`, otherwise
   exactly one file named `codex.exe` anywhere under the source (or, when
   several match, exactly one of them under `resources`); and
   `codex.real.exe` must not already exist beside it.
4. **Tools and archive plan.** `go`, `node`, and `npm` on `PATH`;
   `node_modules/@electron/asar` at the version `package.json` pins; a `node`
   new enough for it. Then the source archive is listed with
   `asar list --is-pack` and the `--unpack` / `--unpack-dir` patterns are derived
   from that listing (a path containing characters asar would read as a pattern
   stops the run), still before anything is copied.
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
   staging directory (`destination.parent\.csr-XXXXXXXX\app\…`) plus an
   eight-character margin, must stay below 260 unless
   `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled` reads
   as `1`; an unreadable value counts as disabled.
10. **Build.** The source is copied into the staging directory, a
    `TemporaryDirectory` beside the destination that is discarded with
    everything in it when any later step fails; the mux is built with
    `GOOS=windows` and the launcher with the flags above.
11. **Archive.** The archive is extracted (the original packed/unpacked state was
    recorded in step 4); `isolate_desktop_profile` (Windows
    prelude), `install_ui_test_bridge`, `retarget_protocol_scheme`, and
    `patch_renderer` run with their exact anchors. `retarget_protocol_scheme`
    fails closed: a `setAsDefaultProtocolClient` call whose scheme is not the
    literal `'codex'` stops the patch before the bundle is written; only the
    total absence of any such call warns.
12. **Repack.** The archive is packed with the `--unpack` / `--unpack-dir`
    patterns from step 4. Afterwards the repacked archive must keep exactly the
    official unpacked set (a missing path cannot be loaded; an extra one lands on
    disk as a file or directory the official layout never had), and the unpacked
    tree must exist exactly when a pattern was given.
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
16. **Icons.** The router's three icon files replace the official window and
    tray icons that the staged copy ships (only files that exist are replaced) and
    `Codex Router.ico` is placed beside the launcher for the shortcuts. A missing
    router icon in the repository is an error, since all three are tracked.
17. **Shortcuts.** One PowerShell command creates the Start menu shortcut and, unless
    `--no-desktop-shortcut`, the Desktop one (the Desktop folder is asked of Windows
    because it can be redirected, and a missing one is skipped), and removes a
    `Codex Subscription Router.lnk` whose target is this launcher. Best effort; a
    failure is a warning because the install is already launchable.

`--check-source` stops after step 6 (source discovery and approval) without copying or
building anything and exits 3 when the build is not recorded, 0 otherwise, 1 for any
other failure. `install.ps1` runs it first and asks the person before going on.

## Unverified assumptions

Items marked **Observed** were confirmed on the Store build `26.930.3930.0`
(see [Observed on the Store build](#observed-on-the-store-build)); the rest have
not been observed on any official Windows build. Each is either checked at run
time (and stops the install when wrong) or is marked as not checkable.

Official app layout:

- **Observed.** The official app is a Microsoft Store package, found through the
  package registry; `--source` overrides. (The first design assumed the
  opposite and refused it.)
- **Observed.** The host is the executable the package manifest names
  (`ChatGPT.exe`, Chromium's launcher), and `codex.exe` exists once under
  `resources` plus a 20 KB `Codex.exe` stub beside the host. Both have
  overrides.
- When `codex.exe` lives inside `app.asar.unpacked`, the repacked archive
  header keeps the official binary's recorded size and SHA-256 for that path
  and lists no `codex.real.exe`; harmless because Electron reads unpacked
  entries from disk without checking them, but the header is not a
  description of the installed binary. Not checkable by the patcher, and not
  the case on the Store build, where `codex.exe` is in `resources`.
- **Observed.** The main-process bundles carry the profile (`setPath('userData')`,
  one `bootstrap-*.js`) and updater (`initializeUpdater`) anchors; both matched.
  The Store updates the app itself, so Squirrel entry points do not apply there;
  any other Windows-only update path is not detectable and must be reviewed by
  hand.
- **Observed.** Protocol registration exists once, in the exact win32-guarded
  early-return shape described above, so it never runs on Windows. Any other
  `setAsDefaultProtocolClient` call still stops the patch; only the total absence
  of such a call warns.
- **Observed.** The renderer matches through the chunked-renderer path (12246
  and later) once the memo-cache identifier is captured.
- **Observed.** `resources\app.asar.unpacked` contains only `node_modules`
  packages. The patch no longer relies on this: unpacking follows the official
  archive's own listing.
- **Observed.** The `INTEGRITY` resource is a JSON array with one
  `{file: "resources\app.asar", alg: "SHA256", value}` entry, rewritten with the
  repacked header digest (the copy's `exe-info` shows the recorded value and
  `signed: false`), and the copy starts. No embedded-digest sentinel exists in
  `chrome.dll`, `ChatGPT.exe` or `chrome_elf.dll`.
- **Observed.** The host's `RT_VERSION` carries `ProductVersion`, `FileVersion`
  and `ProductName` (`Codex`); the versions are Chromium's, not the app's.
- **Observed.** The Windows build does not use `SKY_CUA_SERVICE_NATIVE_PIPE_PATH`
  or `CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH`: the main bundle reads
  them only when the platform is `darwin`, and the Windows target of `@oai/sky`
  (`resources\cua_node\bin\node_modules\@oai\sky`) spawns `codex-computer-use.exe`
  as a child over stdio. The first version of the Windows prelude set both anyway,
  on the assumption that a named pipe was involved; it was inert and is gone. The
  same bundle enables Windows Computer Use only when
  `CODEX_ELECTRON_ENABLE_WINDOWS_COMPUTER_USE` is `1`, though Settings has a
  "Computer use" page either way. The copy follows whatever the official app does.
- **Observed.** The helper works from the copy: with `CODEX_CLI_PATH` set to the
  router's multiplexer (what the app hands it in the copy) the bundled node runtime
  started it, and `sky.list_windows()` returned the open windows. The helper starts
  `codex app-server` itself, so it runs a second multiplexer with its own engines
  beside the app's; the second one finds port 48123 taken, says "account UI
  unavailable" and carries on. A model-driven Computer Use turn was not run: the
  helper reports every open window's title to the model, which the user did not
  ask for.
- **Observed.** The official bundled `codex.exe` and the multiplexer are both
  console-subsystem PEs (subsystem 3), so spawning the mux is like spawning the
  official engine. Whether a console window ever flashes was not observed.

Run-time behaviour:

- The Windows `codex.exe` app-server exits promptly on stdin EOF, as the
  macOS one does; hence the 2 s grace and kill backstop. **Observed:** ending the
  host process left no router process behind and freed port 48123 within a
  second. Closing the window does not quit the app: it keeps running, hidden, and
  launching it again brings the window back. The app registers a tray icon with a
fixed GUID per build flavour, and Windows binds a tray GUID to the executable that
first registered it. The first build of the copy reused the official GUID from
another path: the notification-area registry listed that GUID for the official
`ChatGPT.exe` only, so the copy had no tray icon and nothing to quit it from. The
patcher now replaces each known tray GUID with a stable GUID of the router's own
(`retarget_tray_identity`; each literal appears exactly once in the main bundle,
more than once stops the patch, none prints a warning). After a rebuild the
registry lists `{BC771F8D-7AD0-5934-B870-47A7FA8AAE79}` for the copy's executable
and the official GUID for the official one. The tray menu's Quit entry is in the
app's code (`role: quit`); clicking it was not exercised.
- **Observed.** `pe-library` parses the real host executable (4.7 MB) and
  `set-asar-integrity` rewrote it. It refuses PEs with a COFF symbol table and
  unusual resource layouts. One parse holds the file buffer
  plus pe-library's own copies of the sections; `set-asar-integrity` parses
  the input, the temp file, and the final file in turn, so budget several
  times the executable's size for it.
- **Observed.** `fs.renameSync` over the existing `.exe` succeeded (`MoveFileEx`
  with replace); it fails, leaving the target untouched, if the file is locked.
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
- **Observed.** `winreg` reads `LongPathsEnabled` (it was off on the test PC),
  and with it off the default install works because of the short staging path.
- The backup move is a rename when `%USERPROFILE%\.codex-mux` and the
  destination share a volume; across volumes (a `--destination` on another
  drive, a relocated profile) it falls back to a copying move, which is not
  atomic. The staged copy's final rename never crosses volumes because the
  staging directory is created beside the destination.
- `GOARCH` comes from the machine type of the official host executable
  (`x64` builds amd64, `arm64` builds arm64, anything else stops the install), not
  from the toolchain's default, so an emulated x64 Go on an ARM64 PC still builds
  for the app it sits next to. CI cross-compiles both and, on a real
  `windows-11-arm` runner, runs the Go tests and checks that both programs come out
  as ARM64 PE files. The patched app itself has not run on ARM64 hardware: the
  Store package cannot be installed on a runner.
- SmartScreen and Defender behaviour towards the unsigned rewritten executable
  and the unsigned Go binaries is untested.
- Taskbar grouping: pins point at `Codex Subscription Router.exe` while the
  window belongs to the Electron executable; without a shared AppUserModelID
  Windows may show two taskbar entries. The running window carries the router's own
  icon (read back from the window with `WM_GETICON`), so it is distinguishable from
  the official app either way.
- **Observed.** The launcher started the sibling host executable with the
  copy's profile. Its `MessageBoxW` and exit-code propagation were
  cross-compiled and vetted, not executed.

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

## Recording a supported build

Build `26.930.3930.0` was taken through steps 1 to 3 and recorded provisionally
(step 5 and 6 done; step 4 only partly: launch and account screens, not routing,
failover or resume). Step 4 is still open for it, and steps 1 to 6 apply to any
further Store build.

1. On a Windows PC with the official desktop app installed from the Microsoft
   Store, clone the repository, run `npm ci --ignore-scripts`, and run
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
   `("<ProductVersion>", "<FileVersion>"): "<app.asar sha256>"` (a key shared by
   several app builds lists a tuple of hashes), using the whole-file digest from
   the identity line, not the header digest; extend the `approve_source` tests in
   `scripts/test_patch_app_windows.py` to accept it. Keep it labelled
   provisional until step 4 is complete.
6. Add the row to the Windows table in [COMPATIBILITY.md](COMPATIBILITY.md),
   change its architecture row from "untested" to what was tested, update the
   Compatibility section of the README and the status in
   [MAINTAINED.md](MAINTAINED.md), and drop the `--allow-untested-source`
   requirement from the README's Windows instructions for that build.
7. Record what was observed in [Observed on the Store build](#observed-on-the-store-build):
   the host executable, the `codex.exe` path, whether the integrity resource
   existed, the unpacked set, the protocol-registration shape, and the Windows
   build number and tool versions.

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
  the arm64 equivalent must succeed (the `macos` job cross-compiles both).
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
- CI: the `macos` job adds the Windows cross-compile (amd64 and arm64) and the
  PE helper tests; the `windows` job repeats the Go, JavaScript, PE helper, Python,
  and release checks on `windows-latest` and parses `install.ps1`; the
  `windows-arm64` job runs the Go tests and the PE helper tests on a real ARM64
  Windows runner and checks the programs the patcher builds are ARM64.

## Status

Provisional. Built, launched and run on the Microsoft Store build
`26.930.3930.0` on Windows 11 (x64): the patch applies, the copy runs beside the
official app with the multiplexer and one engine per connected account, and the
patched account menu works, including adding a second subscription. All Go,
JavaScript, PE-helper and Python checks pass on Windows (`go test ./...`,
`go vet ./...`, 140 Python tests). The recorded build is in
`TESTED_WINDOWS_SOURCE_BUILDS` and [COMPATIBILITY.md](COMPATIBILITY.md), which
also lists what has and has not been exercised: routing, moves and failover
between two real subscriptions, the one-line installer with a forced rebuild,
shutdown and single-instance behaviour, and the automated end-to-end failover
tests have passed, and so have the usage sheet's per-account reset flow
(with simulated balances) and the per-account plugin connections in the live
window. Not exercised: a real reset redemption (it spends a credit, and the code
is the same HTTP call the macOS build ran against the real API), a model-driven
Computer Use turn, the app on ARM64 hardware, and the reactive failover against
the real engine. Do not drop the provisional label before
[SMOKE-TEST.md](SMOKE-TEST.md) is complete.
