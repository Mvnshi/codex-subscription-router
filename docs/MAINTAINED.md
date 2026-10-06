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

Beyond those: plugin RPC account scoping for the newer native method names,
build 8109 support, and a provisional Windows port ([WINDOWS.md](WINDOWS.md)).

## Supported builds

Versions and `app.asar` hashes are in [COMPATIBILITY.md](COMPATIBILITY.md).
Unsupported source builds are rejected unless `--allow-untested-source` is
passed. Build 8109 is provisional — it patches, signs and launches, and loads
connected accounts, but multi-account routing has not been exercised on it.
See [BUILD-8109-PORT.md](BUILD-8109-PORT.md). One Windows build is recorded,
provisionally: the Microsoft Store package `OpenAI.Codex` `26.930.3930.0`
(build `12947`). It patches, launches beside the official app, connects
accounts, and routes, switches and moves chats between two real subscriptions
(checked live, plus automated end-to-end tests), and its per-account reset flow
and plugin connections work in the live window; a real reset redemption, a
model-driven Computer Use turn and the app on ARM64 hardware have not been
exercised. Any other Windows build is refused until `--allow-untested-source` is
passed.

## Known gaps

1. Exercise 8109 beyond launch: routing across accounts, failover when one is
   exhausted, thread resume and Computer Use.
2. [PR #23](https://github.com/b-nnett/codex-subscription-router/pull/23) changes
   thread storage and persistent indexes. Migration backup, resume, ownership
   and rollback need dedicated testing before it is taken.
3. Model-aware routing needs timeout, transient-error, missing-model,
   pagination, failover and sticky-ownership tests before integration.
4. The signed desktop smoke matrix should run before any release tag.
5. Windows: finish the smoke test on build `12947` (a real reset redemption, the
   reactive failover against the real engine) and drop the provisional label, as
   described in [WINDOWS.md](WINDOWS.md). Also run the app on ARM64 hardware
   (CI already tests the programs there), and decide how to carry the
   Store-managed Windows sandbox service (see WINDOWS.md) into the copy.

## Install

Use the command in the root README. It fetches this fork's `main`, keeps its
own source checkout, installs the standard router app names and reuses existing
router state. Installing replaces an existing bundle and keeps a recoverable
backup; it does not create a second account pool.
