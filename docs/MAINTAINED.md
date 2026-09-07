# Maintained fork

This fork's `main` is a maintained development branch, not an official upstream
release. The MIT license and original notices are intact, and contributor
commits are preserved with their original authorship.

## Included community work

- [Upstream PR #24](https://github.com/b-nnett/codex-subscription-router/pull/24):
  desktop build 7746 compatibility, account-list responsiveness, pooled usage,
  shutdown behavior and signing updates. Its history also preserves earlier
  contributions by Dekiruyo and braindead-dev.
- [Upstream PR #17](https://github.com/b-nnett/codex-subscription-router/pull/17):
  macOS Bash 3.2 first-install fix by hhh2210.
- [Upstream PR #25](https://github.com/b-nnett/codex-subscription-router/pull/25):
  signing-team detection and regression tests.

Beyond those: plugin RPC account scoping for the newer native method names, and
build 8109 support.

## Supported builds

Versions and `app.asar` hashes are in [COMPATIBILITY.md](COMPATIBILITY.md).
Unsupported source builds are rejected unless `--allow-untested-source` is
passed. Build 8109 is provisional — it patches, signs and launches, and loads
connected accounts, but multi-account routing has not been exercised on it.
See [BUILD-8109-PORT.md](BUILD-8109-PORT.md).

## Known gaps

1. Exercise 8109 beyond launch: routing across accounts, failover when one is
   exhausted, thread resume and Computer Use.
2. [PR #23](https://github.com/b-nnett/codex-subscription-router/pull/23) changes
   thread storage and persistent indexes. Migration backup, resume, ownership
   and rollback need dedicated testing before it is taken.
3. Model-aware routing needs timeout, transient-error, missing-model,
   pagination, failover and sticky-ownership tests before integration.
4. The signed desktop smoke matrix should run before any release tag.

## Install

Use the command in the root README. It fetches this fork's `main`, keeps its
own source checkout, installs the standard router app names and reuses existing
router state. Installing replaces an existing bundle and keeps a recoverable
backup; it does not create a second account pool.
