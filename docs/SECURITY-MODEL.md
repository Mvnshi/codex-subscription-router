# Security model

## Trust boundaries

- The official ChatGPT app is trusted build input and remains unchanged.
- The patchers have local filesystem access by design; the macOS patcher also
  has code-signing access.
- Each real Codex child is trusted with only its assigned account home.
- The injected renderer is trusted with the loopback control token.
- Other local users and remote origins are outside the control API boundary.
- Processes running as the same macOS or Windows user are not considered
  isolated from one another; they can already read that user's app data
  subject to macOS permissions or NTFS ACLs.

## Credentials

OAuth material stays in `auth.json` under each account's Codex home. The
multiplexer reads an account token only to call the same authenticated ChatGPT
profile and rate-limit-reset endpoints used by the desktop experience. It does
not log or return tokens. State persisted by the mux contains account paths,
labels, enabled state, and thread ownership only.

The state root is mode `0700`; state, config, and control-token files are mode
`0600`. Existing control tokens are validated as 256-bit hexadecimal values and
their permissions are repaired on startup.

Plugin and MCP configuration is deliberately synchronized from the Primary
account so installed definitions remain consistent. Inline environment values
inside those definitions are therefore copied into every isolated account home
with mode `0600`; account isolation is not a separate secret boundary for
shared plugin configuration.

## Network

The control server binds to `127.0.0.1`. Private endpoints require the token
embedded into the independently built local renderer. Profile images must use
HTTPS. Response sizes and JSON request bodies are bounded.

The project itself does not provide a telemetry or update endpoint. Network
traffic beyond loopback is performed by the official Codex children or by the
documented ChatGPT profile and rate-limit APIs.

## Signing and native access

The source app is copied into a temporary staging directory. Native modules,
the Computer Use helper, Node runtime, mux, and final app are signed under one
selected Apple team and verified before replacement. Official OpenAI
application-group and keychain entitlements are removed from modified callers.

The native helper's caller allowlist is patched to the selected team and the
independent desktop bundle ID. This is required for the helper's peer checks;
it does not bypass macOS Accessibility or Screen Recording consent.

## Diagnostics

`CODEX_MUX_UI_TESTS=1` enables deterministic preview and screenshot endpoints.
They are unavailable during a normal launch, bind only to loopback, and require
the same control token. Release workflows never set this variable.

## Distribution

Releases contain source only. Publishing the patched `.app`, the official ASAR,
or any extracted OpenAI binary is outside this project's release process.

## Windows

The Windows port is provisional (see [WINDOWS.md](WINDOWS.md)); the properties
below describe what the code does, not what has been observed on an official
build.

- **No code signature on the copy.** Recording the repacked archive's header
  digest means rewriting the executable's `INTEGRITY`/`ELECTRONASAR` resource,
  and the PE library used for that drops the Authenticode signature. The
  copied Electron executable, the Go launcher, and the Go multiplexer are all
  unsigned. SmartScreen may warn on first launch. There is no `codesign
  --verify` equivalent; `node scripts/win/exe-info.mjs <exe>` reports
  `signed: false` and the recorded integrity value, nothing more.
- **State root ACL.** The patcher creates `%USERPROFILE%\.codex-mux` and
  writes `control-token` into it (`load_or_create_token`, shared with macOS),
  then immediately, before anything else is installed, runs
  `icacls %USERPROFILE%\.codex-mux /inheritance:r /grant:r <DOMAIN\user>:(OI)(CI)F *S-1-5-18:(OI)(CI)F`:
  inheritance is removed and only the current user and SYSTEM keep access.
  `icacls` recomputes the inherited ACEs of the token already inside, and
  `(OI)(CI)` makes files and directories created later under the root (state,
  account homes, the `backups\<timestamp>\` directory) inherit that ACL.
  Until `icacls` has run, the token carries the ACL inherited from
  `%USERPROFILE%` (by default the current user, Administrators, and SYSTEM).
  A previous install is moved into the backup directory with a same-volume
  rename, so that tree keeps the ACL it had under `%LOCALAPPDATA%\Programs`
  (the same three principals by default) rather than inheriting the root's.
  The install stops if `icacls` fails.
- **POSIX modes are no-ops.** The multiplexer's `0700`/`0600` modes and the
  control-token permission repair on startup only toggle the read-only
  attribute on Windows. Protection of the state root therefore comes from the
  install-time ACL and NTFS inheritance, not from the mux. The Primary home
  `%USERPROFILE%\.codex` belongs to the official app and is not re-ACLed.
- **Control server unchanged.** It binds `127.0.0.1:48123` and requires the
  same random 256-bit token; the token lives in `%USERPROFILE%\.codex-mux`.
- **Same-user processes are not isolated** from each other, as on macOS. The
  copy is installed per user under `%LOCALAPPDATA%\Programs`, so any process
  running as that user can modify it; the installer refuses to run elevated.
- **Computer Use.** No Windows helper is patched or shipped. The copy's
  `SKY_CUA_SERVICE_NATIVE_PIPE_PATH` is set to
  `\\.\pipe\codex-subscription-router-computer-use`, a named pipe the
  official app never uses, so the copy cannot attach to the official helper's
  pipe by accident; nothing listens there.
- **Protocol handler.** The copy registers `codex-subscription-router://`
  instead of `codex://` (registry keys under `HKCU\Software\Classes`), so
  deep links keep opening the official app. The patcher warns rather than
  fails when it finds no registration call to retarget; the smoke test checks
  the handler after launch.
- **Profile isolation.** The launcher passes
  `--user-data-dir=%APPDATA%\Codex Subscription Router` and the main process
  sets the same `userData`; Electron's single-instance lock is scoped to that
  profile, so the copy and the official app run side by side.
