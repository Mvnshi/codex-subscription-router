# Porting notes: desktop build 12246

Source: ChatGPT `26.928.20755` build `12246`, `app.asar` SHA-256
`2301fba40bd8fa237ccdb1369363e1deefaf27953da2d767d428225d5e9eedee`.

## Layout changes

The profile menu, usage sheet and reset-credit helpers are now separate lazy
renderer chunks. The patcher locates their imports and verifies each anchor
before patching. The main-process updater startup expression also changed.

The bundled engine moved from `Resources/codex` to
`Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`. The wrapper in
`codex-cli/bin/codex` forwards to that executable. Replace the executable that
the app actually launches and re-sign the CLI bundle after removing its
OpenAI provisioning profiles.

## Integrity failure and fix

The newer framework (Chromium `154.0.8037.57`) enables a second integrity
check: it authenticates the `ElectronAsarIntegrity` dictionary against a
digest embedded in the framework. Updating only the archive header hash in
`Info.plist` leaves this digest stale. Electron then aborts with
`Failed to get integrity for validatable asar archive: Resources/app.asar`
before comparing the archive header hash.

The official framework's enabled version-1 slot matches the original plist;
the failed router retained that same slot after changing the plist. The older
framework (`152.0.7977.83`) contains an unused slot, explaining why updating
only the plist worked there. The archive header hashing function is unchanged.

[`integrity_digest.mm`](https://github.com/electron/electron/blob/main/shell/common/asar/integrity_digest.mm)
defines the slot: the 32-byte sentinel `AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A`,
one used byte, one version byte, and a 32-byte SHA-256 digest. The digest covers
the UTF-8 path, algorithm and hash strings concatenated in literal path order.
`patch_electron_integrity_digest` updates enabled version-1 slots after writing
the plist and before signing. It preserves the enabled flags and rejects
unsupported or truncated slots.

## Verification

On 2026-09-29, the full automated checks passed, including four regression
tests covering enabled slots, older frameworks, multiple architecture slots,
ordering, and malformed slots, plus two loaded-modal selection tests. Both
fixes received independent code reviews.

A complete independently signed build passed signature verification and
launched in a separate test profile using `--use-mock-keychain`. The renderer
loaded the new onboarding questionnaire without the integrity failure or a
new macOS crash report. The installed working router was left running.

The new onboarding flow initially prevented the UI smoke-test bridge from
reaching the account menu. Setting the isolated test profile's local persisted
onboarding state bypassed it without submitting remote onboarding preferences.
The account menu showed all three subscriptions, plans, usage and credits;
profile and plugin pages loaded; and the Usage sheet displayed reset balances.

The live Usage-sheet test then exposed a second bug: selection changed the
global account scope but the native sheet stayed on the first account. The
selection hook ran above a cached lazy component boundary. Moving it into the
loaded modal and keying its child by selected account fixes propagation and
resets native form state when changing accounts. The previously failing
`usage-select-second` check passed after the fix, showing the second account's
usage windows, credit balance and reset credits with no local renderer errors.

## Routing and history transfer

Real GPT-6.1-Sol turns passed on all three accounts, including pinned new chats,
sticky follow-ups and same-account resume. Simulated depletion of the owner
then reproduced a new-engine transfer failure: the engine no longer accepts a
foreign rollout path for a paginated chat. Its generated public resume schema
omits path/history, although the engine still checks an undocumented path
against its active or persisted account-local history.

The router keeps the legacy path-based resume first. On a missing rollout or
an explicit stale-path error, it copies the current owner's complete rollout
atomically into the target's session directory and resumes with the same chat
ID and the target-local path. When revisiting an account, the previous writer
must stop first: unsubscribe only detaches notifications and leaves it alive.
Archive waits for shutdown; unarchive restores visibility before copying and
resuming. Without shutdown, the engine writes to the replaced file's old inode
and later reads lose the new turn. Destination validation rejects paths outside
sessions and symlinked session parents.

Completion notifications also carry top-level threadId rather than thread.id.
Parsing both shapes clears the router's in-flight marker, allowing manual moves
after a response completes.

Live checks passed for depleted-owner failover, sticky continuation, moves back
and forth while recalling messages from both accounts, and the combined error
when all subscriptions are depleted. Only test quota previews were changed;
the preview was cleared and the smoke-test chats archived afterward. The
installed router's preferred account and existing chat assignments were kept.

No real resets were redeemed. Actual reset redemption and desktop Computer Use
remain unverified: macOS Accessibility and Screen Recording approval is still
pending. These limits keep the build provisional despite the passing routing
and screen checks.

The reset-count follow-up was checked against live read-only responses: the
picker showed 3, 1 and 2 resets. Selecting Subscription 2 changed the native
sheet to 1 available, matching its picker row. Pending counts now show loading
and settle independently; native and picker queries share pending requests.
