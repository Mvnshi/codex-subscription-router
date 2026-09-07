# Maintained fork

This fork’s `main` is Mvnshi’s maintained development branch, not an official
upstream release. The MIT
license and original notices remain intact. The branch preserves contributor
commits; integration does not transfer credit for their work to the maintainer.

## Included community work

- [Upstream PR #24](https://github.com/b-nnett/codex-subscription-router/pull/24):
  full desktop build 7746 compatibility, account-list responsiveness, pooled
  usage, shutdown behavior, and signing updates. Its history also preserves
  earlier contributions by Dekiruyo and braindead-dev.
- [Upstream PR #17](https://github.com/b-nnett/codex-subscription-router/pull/17):
  macOS Bash 3.2 first-install fix by hhh2210.
- [Upstream PR #25](https://github.com/b-nnett/codex-subscription-router/pull/25):
  signing-team detection and regression tests by Mvnshi.
- Integration fix: newer native plugin RPC names now receive the selected
  account scope, with coverage for legacy aliases and unchanged unrelated calls.

## Verification and limits

Automated Go tests, Go race detection, JavaScript tests, Python regression
tests, release metadata checks, and native launcher syntax checks pass locally.
Exact input versions and hashes remain in [COMPATIBILITY.md](COMPATIBILITY.md).

On 2026-09-07, a fresh build from the official build-7746 ZIP matched the
recorded ASAR hash and completed without an untested-source override. Both
the generated app and standalone Computer Use helper passed
`codesign --verify --deep --strict`. This verification did not launch the
new build or exercise its full desktop/Computer Use matrix.

Build 8109 (`26.901.51231`) was ported the same day and now patches, builds
and signs cleanly from the official app with no untested-source override; see
[BUILD-8109-PORT.md](BUILD-8109-PORT.md) for every re-derived anchor and how it
was validated. Launching that build surfaced a real defect: the patcher recorded a whole-file
asar digest where Electron validates the header digest. Harmless on earlier
builds, fatal on 8109, which enables the integrity fuse. With that fixed the
8109 build launches, loads every connected account, and offers GPT-6 Astra.
Routing, failover, thread resume and Computer Use remain untested on 8109.

The currently installed local mixed-runtime experiment is not the source of
this maintained fork. A newer backend can expose Astra without making the
older renderer and thread-resume protocol compatible. No newer runtime is
substituted by this installer.

## Next integration work

1. Exercise 8109 beyond launch: routing across accounts, failover when one is
   exhausted, thread resume, and Computer Use. Launch, account load and model
   availability are verified; multi-account behaviour is not.
2. Evaluate [PR #23](https://github.com/b-nnett/codex-subscription-router/pull/23)
   separately: it changes thread storage and persistent indexes, so migration
   backup, resume, ownership and rollback need dedicated testing.
3. Integrate model-aware routing only after timeout, transient-error, missing
   model, pagination, failover and sticky-ownership tests pass.
4. Run the signed desktop smoke matrix before tagging a release.

## Install and updates

Use the command in the root README. It fetches this fork’s `main` branch
and uses a separate source checkout at `~/.codex-subscription-router-mvnshi/source`.
It still installs the standard router app names and reuses existing router state.
Installing replaces an existing router bundle with a recoverable backup; it
does not install a second independent account pool. Unsupported source builds
remain rejected by default.
