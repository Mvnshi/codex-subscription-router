# Compatibility

The patcher is intentionally tied to known ChatGPT desktop bundle structures.
It verifies every modified renderer, main-process, and native binary anchor and
stops instead of applying a partial patch.

## Release 0.1.0

| Official ChatGPT version | Official bundle build | `app.asar` SHA-256 |
| --- | --- | --- |
| `26.803.61601` | `6396` | `d5a44ed9e2f1db5f81dbbe85408aed256f3203c5b16f00817bb9d7cd941343cf` |
| `26.810.52044` | `6662` | `6e7e8791b8bf69a586ff994721fff518af391d9efdc66cd2e620dd2a4aedc90f` |
| `26.901.22334` | `7746` | `405f0e1600fc63851abe4c763ec0546f56c32da312c2c2745e2b997c579ce0d0` |
| `26.901.51231` | `8109` | `64fc2f27d2dddfa968acfacbe5e4e0328071bdc406351ff4a7d18f0b4692c83d` |
| `26.928.20755` | `12246` | `2301fba40bd8fa237ccdb1369363e1deefaf27953da2d767d428225d5e9eedee` |

Build `12246` is provisional: patching, signing, isolated launch and account,
usage, profile and plugin screens, routing on three accounts, failover and
history-preserving account moves, native desktop clicks and keyboard input,
and real reset redemption through the account API passed. The native Use reset
button flow remains untested because Computer Use protects its host app. See
[BUILD-12246-PORT.md](BUILD-12246-PORT.md) for the integrity fix and test limits.

| Component | Tested value |
| --- | --- |
| Architecture | Apple silicon (`arm64`) |

A different official version may work when all anchors remain identical, but
it is unverified. The patcher rejects a version, build, or ASAR hash mismatch by
default; `--allow-untested-source` is an explicit diagnostic override. Never
weaken an anchor-count or binary-constant check merely to make a new build
complete. Review the upstream change and update the patch deliberately.

## Recorded builds

OpenAI ships a new build of the app about every day, on macOS through an update feed and on
Windows through the Microsoft Store (the Store title is now "ChatGPT"; the package is still
`OpenAI.Codex`). The two platforms share app builds: Store package `26.930.3930.0` is app
`26.930.31730`, build `12947`, the same build the macOS feed lists.

Two levels are recorded, and the installers treat them differently:

- **Tested by hand**: the tables in the sections below, checked against
  [SMOKE-TEST.md](SMOKE-TEST.md) as far as each section says.
- **Verified by the canary**: a build that the daily canary
  ([upstream-canary.yml](../.github/workflows/upstream-canary.yml)) installed on a clean machine,
  ran the real installer against with every patch anchor matching, and, on Windows, booted (control
  API healthy, window loaded). Routing, switching and failover were not re-run on it, so it is a
  weaker statement than a tested build, but it is enough to install without being asked.

A build in neither list is not refused: the installers explain that it is newer than the recorded
builds and ask. Every patch step still checks the app and stops by itself if it changed. This table
is generated from `scripts/recorded_builds.json` by `scripts/record_build.py`; see
[MAINTAINING.md](MAINTAINING.md) for how builds get recorded.

<!-- recorded-builds:begin -->
| Platform | Official build | App version / build | `app.asar` SHA-256 | How far it was checked |
| --- | --- | --- | --- | --- |
| Windows x64 | `OpenAI.Codex` `26.930.6422.0` | `26.930.51102` / `13100` | `bdff0036791292cb315ff25c2b836ba35addedd2327f25dd471c38df791194dd` | verified by the canary (patched with every anchor matching; on Windows also booted) |
| Windows x64 | `OpenAI.Codex` `26.930.3930.0` | `26.930.31730` / `12947` | `af98213984ec4556778ef9276193d51460153fb9b30fded882d503637b84abba` | tested by hand |
<!-- recorded-builds:end -->

## Windows (provisional)

| Official package | App version / build | Host `FileVersion` (Chromium) | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `OpenAI.Codex` `26.930.3930.0` (Microsoft Store) | `26.930.31730` / `12947` | `154.0.8037.98` | `af98213984ec4556778ef9276193d51460153fb9b30fded882d503637b84abba` |

This build is recorded in `TESTED_WINDOWS_SOURCE_BUILDS` in
`scripts/patch_app_windows.py`, so the patcher accepts it without
`--allow-untested-source`; any other build is refused until that flag is passed.
The key is the `(ProductVersion, FileVersion)` pair from the host executable's
version resource, which on the Store build is Chromium's `chrome.exe` launcher,
so it is the Chromium runtime version; the hash is the SHA-256 of the whole
official `resources\app.asar`, as printed on the patcher's identity line, and is
the real identity.

Build `12947` is **provisional**. Passed, on Windows 11 Pro 10.0.26100 (x64) with
Go 1.27.0, Node.js 24.17.0 and Python 3.12.10:

Build and install

- patching: the shared renderer and main-process anchors match, after two
  fixes recorded in [WINDOWS.md](WINDOWS.md) (a renamed memo-cache identifier in
  the Usage sheet anchor, and the win32-guarded protocol registration)
- repacking reproduces the official unpacked set exactly (48 entries of 21,032)
- the official package's `app.asar` hash and Authenticode signature are
  unchanged after the build
- the real one-line installer, run from `main` over a running install with no
  override flag: it cloned the source, stopped the old copy, rebuilt with
  `--force`, kept the previous copy as a 2.1 GB backup under
  `%USERPROFILE%\.codex-mux\backups`, relaunched, and left accounts, chat
  ownership and the routing setting as they were

Launch and lifecycle

- the copy launches while the official app is running, the multiplexer runs as
  `resources\codex.exe` with `codex.real.exe` beneath it and answers `/v1/health`
- a second launch does not start another instance (it exits, the process set is
  unchanged), and launching again after the window was closed brings it back
- ending the host process leaves nothing behind: the multiplexer, every engine
  and port 48123 were gone within one second. Closing the window only hides the
  app, which keeps running in the background with its own tray icon: the shell's
  notification-area registry lists the router's own tray GUID for the copy's
  executable and the official GUID for the official one. The tray menu's Quit
  entry comes from the app's code and was not clicked
- the patched profile menu shows both subscriptions (plan and usage) and
  "New chats use Automatic"; a second subscription was added through it and both
  accounts report enabled and connected on the control API
- the official app's `codex://` handler, Chrome native-messaging registration and
  profile were not changed by building or running the copy

A later Store build. While this work was being done the Store updated the package to
`26.930.6422.0` (`app.asar` `bdff0036791292cb315ff25c2b836ba35addedd2327f25dd471c38df791194dd`).
The installer reported it as not recorded, asked, and built from it with
`--allow-untested-source`: every anchor matched, the copy launched with the router's own
icons and shortcuts, and the control API answered with both accounts connected and the
routing setting intact. It is not in `TESTED_WINDOWS_SOURCE_BUILDS`: the routing, switching
and failover checks below were run on `26.930.3930.0`.

Routing, switching and failover, live. A headless client drove the router's real
multiplexer over stdio, with the desktop app's handshake, against two real
subscriptions (Pro 5x with its weekly limit reached, Plus fresh), using
throwaway chats pinned to the built-in provider and archived afterwards. All 12
checks passed:

- a new chat on Automatic went to the account with usage, and a real turn ran
  there and answered; the chat stayed on that account
- moving that chat to the other account did a real history transfer (the history
  file appeared in the target account's home)
- a follow-up in a chat owned by the account whose limit was reached was moved to
  the account with usage before it was sent, answered with the earlier context
  intact, and the client saw no usage-limit error
- pinning the spent account fell back for a new chat; pinning an account with
  usage was honoured

Automated: seven end-to-end tests (`internal/mux/failover_e2e_test.go`) run in CI
on every OS against real child processes and real files. They cover the same
decisions plus the reactive paths (a turn killed by a usage limit, a `turn/start`
rejected with one, every account spent, follow-ups staying put). Four deliberate
breakages of the router (ignoring usage-limit notifications, forgetting the new
owner after a move, skipping the capacity check, moving a chat without its
history) were each caught by them.

Resets, plugins and Computer Use, live. The real window was driven through the
app's own UI-test bridge and its debug port, with simulated reset balances on both
accounts so that no real credit could be touched:

- the Usage sheet lists each subscription with its plan and reset count, switches
  the selected account, and shows that account's windows and resets
- "Use reset" asks for the in-app confirmation, then the router's own "Ask me"
  confirmation naming the account; pressing OK took one reset from the selected
  account (2 to 1) and left the other account's balance alone
- Settings, then Plugins, shows the "Plugin connections" picker; choosing
  Subscription 2 scoped the page to it ("Connection access below is for
  Subscription 2") and the app count changed from 8 to 12. Through the protocol,
  `mcpServerStatus/list` scoped to each account came back with different lists
  (5 and 2 entries)
- the Computer Use helper starts from the copy and lists windows; see
  [WINDOWS.md](WINDOWS.md) for what the Windows build does and does not read

Two things the check found. A plugin request scoped to an account that was
removed or paused used to be answered by Primary, so a stale picker could show
or start a login for the wrong account; it now fails with an error, covered by
`internal/mux/plugin_scope_e2e_test.go`. And the bridge's account-row matcher
missed a row whose plan name ends in a letter ("Pro 5x1 reset available"); fixed.
`app/list` could not be compared per account through the protocol because the
ChatGPT connectors endpoint answered 403 to the unmodified engine too.

Not yet exercised on Windows:

- the reactive path against the real engine, where a turn is sent to an account
  that then reports its limit. The router's usage check moves chats first, and
  the preview mode can only make accounts look spent, not healthy, so only the
  automated tests cover it
- a real reset redemption: it spends a credit, and the account had none worth
  using (it was at 0% used). The code is the same Go HTTP call the macOS build ran
  against the real API; only the Windows window around it was exercised
- a model-driven Computer Use turn (the helper reports every open window's title
  to the model, which was not asked for), and Computer Use being off by default in
  OpenAI's Windows build
- the patched app on ARM64 hardware. The multiplexer and launcher are built for
  the architecture of the official host, and CI runs the Go tests on a real
  `windows-11-arm` runner and checks both come out as ARM64
- how the engine sandboxes commands without the Store's sandbox service

Run [SMOKE-TEST.md](SMOKE-TEST.md) to complete them.

| Component | Tested value |
| --- | --- |
| Architecture | `x64` (`arm64`: programs built and tested in CI, app not run on ARM64 hardware) |

The procedure for recording further builds is in [WINDOWS.md](WINDOWS.md).
