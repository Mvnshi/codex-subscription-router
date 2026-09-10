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

| Component | Tested value |
| --- | --- |
| Architecture | Apple silicon (`arm64`) |

A different official version may work when all anchors remain identical, but
it is unverified. The patcher rejects a version, build, or ASAR hash mismatch by
default; `--allow-untested-source` is an explicit diagnostic override. Never
weaken an anchor-count or binary-constant check merely to make a new build
complete. Review the upstream change and update the patch deliberately.

## Windows (provisional)

| Official version | FileVersion | `app.asar` SHA-256 |
| --- | --- | --- |

No row exists yet: no official Windows build has been exercised, so
`TESTED_WINDOWS_SOURCE_BUILDS` in `scripts/patch_app_windows.py` is empty and
the Windows patcher refuses every source unless `--allow-untested-source` is
passed. The key is the `(ProductVersion, FileVersion)` pair from the Electron
executable's version resource; the hash is the SHA-256 of the whole official
`resources\app.asar`, as printed on the patcher's identity line.

| Component | Tested value |
| --- | --- |
| Architecture | `x64` / `arm64` (untested) |

The procedure for recording the first build is in [WINDOWS.md](WINDOWS.md).
