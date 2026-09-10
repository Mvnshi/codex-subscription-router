# Contributing

## Development setup

On macOS, use Apple silicon with Go 1.26+, Node.js 22.12+, npm, Xcode Command
Line Tools, and an official ChatGPT installation.

On Windows, use PowerShell 5.1 or 7 with Go 1.26+, Node.js 22.12+, npm, Python
3.11+, git, and an official per-user ChatGPT desktop installation. The Windows
port is provisional: no official Windows build has been exercised yet, so
`scripts/patch_app_windows.py` requires `--allow-untested-source` and checks
every layout assumption at run time. Read `docs/WINDOWS.md` first.

```sh
npm ci --ignore-scripts
npm run check
npm run release:check
```

`npm run check` runs the Go tests and vet, the JavaScript syntax and unit
checks, the Windows PE helper tests (`check:win`, which cross-compile a Go
fixture and therefore need `go` on `PATH`), the Python compile and unit tests
for both patchers, and the shell installer syntax check. It runs on macOS and
Linux; `check:python` and `release:check` call `python3` and `check:shell`
calls `bash`, which stock Windows does not provide, so on Windows run the
per-tool commands listed under "Development and verification" in `README.md`
(the same ones the `windows` CI job runs).

Do not commit an app bundle, a patched executable, credentials, signing
certificates, provisioning profiles, account state, or captures containing
unmasked email addresses or device codes.

## Patch changes

Renderer and main-process patches depend on exact upstream anchors. A change
must:

1. Keep the official app immutable.
2. Fail closed when an expected anchor or binary constant is absent.
3. Preserve account isolation and sticky thread ownership.
4. Keep control services on loopback with token authentication.
5. Add focused tests for backend behavior and a curated screenshot for a new
   user-visible state when appropriate.
6. Keep macOS behaviour byte-identical when changing code the Windows patcher
   shares with it; the Python tests hold exact expectations for that.

Test against the upstream build recorded in `docs/COMPATIBILITY.md`. If a new
official build requires anchor changes, update that file in the same pull
request. A Windows-specific anchor belongs in `scripts/patch_app_windows.py`
with its own exact count, never in a relaxed shared check.

## Pull requests

Keep changes focused and explain security-sensitive behavior explicitly. CI
runs two jobs: `macos` (Go tests and vetting, a Windows cross-compile,
JavaScript syntax, the Windows PE helper tests, Python compilation and tests,
native C syntax, shell syntax, and release metadata consistency) and `windows`
(the same Go, JavaScript, PE helper, and Python checks on `windows-latest`,
plus a PowerShell parse of `install.ps1` and the release metadata check).
