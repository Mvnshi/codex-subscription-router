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

- patching: the shared renderer and main-process anchors match, after two
  fixes recorded in [WINDOWS.md](WINDOWS.md) (a renamed memo-cache identifier in
  the Usage sheet anchor, and the win32-guarded protocol registration)
- repacking reproduces the official unpacked set exactly (48 entries of 21,032)
- the official package's `app.asar` hash and Authenticode signature are
  unchanged after the build
- the copy launches while the official app is running, the multiplexer runs as
  `resources\codex.exe` with `codex.real.exe` beneath it and answers `/v1/health`
- the patched profile menu shows both subscriptions (plan and usage) and
  "New chats use Automatic"; a second subscription was added through it and both
  accounts report enabled and connected on the control API
- the official app's `codex://` handler, Chrome native-messaging registration and
  profile were not changed by building or running the copy

Not yet exercised on Windows: routing a new chat to each account, sticky
follow-ups, depletion failover and history-preserving moves, reset redemption,
plugin account scoping, quitting the copy (no leftover `codex.exe`), a second
launch focusing the running copy, and a `--force` rebuild. Run
[SMOKE-TEST.md](SMOKE-TEST.md) to complete them.

| Component | Tested value |
| --- | --- |
| Architecture | `x64` (`arm64` untested) |

The procedure for recording further builds is in [WINDOWS.md](WINDOWS.md).
