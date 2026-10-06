## Summary

-

## Verification

- [ ] `npm run check` and `npm run release:check` (macOS or Linux), or on Windows the per-tool commands in the README's "Development and verification"
- [ ] The installer tests if an installer or the patchers changed (`bash scripts/test-install.sh`, `powershell -File scripts/test-install.ps1`)
- [ ] Tested against the upstream build in `docs/COMPATIBILITY.md`, or the evidence is in this description
- [ ] If this records an official build: it came from `scripts/record_build.py` on a passing canary result
- [ ] Anything I claim works is recorded in `docs/COMPATIBILITY.md` as verified, and what I did not check is said so
- [ ] No credentials, account data, app bundles, or unredacted device codes

## Security-sensitive changes

Describe any changes to credential handling, subprocesses, network requests,
signing, entitlements, Electron injection, or Computer Use. Write “None” if
there are no such changes.
