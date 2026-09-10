# Architecture

The independently built desktop uses bundle identifier `app.cdxmux.multi`; its
Computer Use helper uses `com.cdxmux.sky.CUAService`. Neither identifier is used
by the official ChatGPT installation. These identifiers and the `.codex-mux`
state directory remain stable across the product rename so existing macOS
privacy grants, connected accounts, and sticky thread ownership continue to
work.

Codex Subscription Router replaces the copied app's bundled `codex` executable
with a small Go multiplexer and keeps the original binary beside it as
`codex.real` (`codex.exe` and `codex.real.exe` on Windows).

## Request routing

The desktop app opens one JSON-RPC app-server connection to the multiplexer.
The multiplexer starts one real app-server child for every enabled account,
each with its own `CODEX_HOME` and `CODEX_SQLITE_HOME`.

New threads are assigned using a quota-urgency score: weekly percentage
remaining divided by the hours until that account resets. Banked usage resets
add a capped bonus, while short-window usage, existing pinned-thread count, and
stable account order break close results. Reset-credit metadata is fetched in
parallel, cached for five minutes, and treated as neutral when unavailable.
Once a thread ID is known, `state.json` persists its owner. Requests, responses,
approvals, and notifications are rewritten only as needed to preserve one
coherent desktop session.

If the owner is depleted, the multiplexer resumes the rollout on an account
with capacity and updates ownership. Threads do not migrate for ordinary load
balancing.

## Account isolation

The Primary account uses `~/.codex`. Added accounts use
`~/.codex-mux/accounts/<id>/codex-home`. Managed configuration is copied from
the Primary account, excluding credential-store settings and project trust.
Each isolated account forces file-backed CLI and MCP OAuth credentials.

## Desktop integration

The patcher extracts `app.asar`, verifies exact upstream anchors, inserts the
account UI, disables self-update, and repacks the archive with an updated
integrity hash. The app receives a separate Chromium profile and URL scheme.

On macOS the copied Computer Use service, Node runtime, and callers are
re-signed under one Apple team. The helper uses a separate bundle identity and
socket, avoiding the official app's privacy grants and app-group container.

## Plugin behavior

Plugin definitions and managed MCP configuration are shared. The Plugins page
adds an account selector and marks Apps, MCP status, and MCP OAuth requests with
the selected account ID. The multiplexer removes that private routing marker
before forwarding the strict RPC request to the chosen child.

## Control API

The renderer talks to a loopback-only HTTP service on port 48123. All private
routes require a random 256-bit token. CORS is limited to the copied app's
`app://-` origin. The service exposes account metadata, aggregated usage and
profile data, thread ownership, login/logout actions, and an authenticated SSE
event stream; it never returns OAuth tokens.

## Platform notes

The Windows port is provisional; see [WINDOWS.md](WINDOWS.md) for what has and
has not been verified. The platform-specific pieces are:

| Piece | macOS | Windows |
| --- | --- | --- |
| Launcher | `native/launcher.c`, compiled by the patcher into `Contents/MacOS/CodexSubscriptionRouterLauncher`; runs `ChatGPT` with the isolated `--user-data-dir` | `cmd/codex-router-launcher` (Go), built as `Codex Subscription Router.exe` with `-H=windowsgui` and `-X main.electronExecutable=<name>`; runs the sibling Electron executable with `--user-data-dir=%APPDATA%\Codex Subscription Router` first and its own arguments after, exits with the child's code, and reports failures in a MessageBox |
| Bundled Codex | `Contents/Resources/codex` becomes the mux; original parked as `codex.real` | the single `codex.exe` under the copy becomes the mux; original renamed `codex.real.exe` beside it. `resolveRealExecutable` tries `codex.real.exe` then `codex.real` on Windows, `codex.real` elsewhere; `CODEX_MUX_REAL_CODEX` overrides |
| Asar integrity | `ElectronAsarIntegrity` in `Info.plist` | `INTEGRITY`/`ELECTRONASAR` resource in the Electron executable, rewritten by `scripts/win/set-asar-integrity.mjs`. Both record the header digest from `asar_header_digest` |
| Child shutdown | `SIGINT` to each child | close the child's stdin, wait up to 2 s, then kill |
| Mux shutdown signals | `SIGINT`, `SIGTERM` | the same list; Go delivers Ctrl-C/Ctrl-Break as `os.Interrupt` and CTRL_CLOSE/LOGOFF/SHUTDOWN as `SIGTERM` |
| Desktop profile | `~/Library/Application Support/Codex Subscription Router` | `%APPDATA%\Codex Subscription Router` |
| State root | `~/.codex-mux` (`0700`) | `%USERPROFILE%\.codex-mux` (NTFS ACL set with `icacls`) |
| Primary account | `~/.codex` | `%USERPROFILE%\.codex` |
| Install location | `~/Applications/Codex Subscription Router.app` | `%LOCALAPPDATA%\Programs\Codex Subscription Router\` |
| Identity | bundle ID `app.cdxmux.multi`, URL scheme edited in `Info.plist` | no bundle identity; URL scheme literal retargeted to `codex-subscription-router` in the main-process bundles |
| Computer Use | helper re-identified and signed | none; the copy is pointed at an unused named pipe |

The control port (48123), token file, state layout, routing, and renderer
patches are the same on both platforms.
