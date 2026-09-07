# Maintained branch

This is Mvnshi’s integration branch, not an official upstream release. The MIT
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

The currently installed local mixed-runtime experiment is not the source of
this maintained branch. A newer backend can expose Astra without making the
older renderer and thread-resume protocol compatible. No newer runtime is
substituted by this installer.

## Next integration work

1. Port and test the full 8109 desktop bundle, with exact source hashes and
   anchor checks, rather than claiming compatibility from the model menu alone.
2. Evaluate [PR #23](https://github.com/b-nnett/codex-subscription-router/pull/23)
   separately: it changes thread storage and persistent indexes, so migration
   backup, resume, ownership and rollback need dedicated testing.
3. Integrate model-aware routing only after timeout, transient-error, missing
   model, pagination, failover and sticky-ownership tests pass.
4. Run the signed desktop smoke matrix before tagging a release.

## Install and updates

Use the command in the root README. It fetches this fork’s `maintained` branch
and uses a separate source checkout at `~/.codex-subscription-router-mvnshi/source`.
It still installs the standard router app names and reuses existing router state.
Installing replaces an existing router bundle with a recoverable backup; it
does not install a second independent account pool. Unsupported source builds
remain rejected by default.
