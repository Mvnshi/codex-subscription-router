# Notes for AI assistants

People often hand this repository to an AI assistant and say "set this up for me". These notes
are for that assistant. They are short on purpose; the real documentation is linked.

Codex Router (the project is called Codex Subscription Router) is a locally patched **copy** of
the official ChatGPT/Codex desktop app that spreads chats across several subscriptions. It never
modifies the official app and ships no OpenAI binaries.

## If you were asked to install it

Use the project's own installer. Do not reimplement it by hand.

**Windows** (a normal, non-elevated PowerShell; the installer refuses an administrator shell):

```powershell
$env:CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES = '1'
irm https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/install.ps1 | iex
```

**macOS** (Apple silicon):

```sh
CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/install.sh)"
```

Before you run it:

- The official app must already be installed: the **Codex** app from the Microsoft Store on
  Windows, the **ChatGPT** app in `/Applications` on macOS. If it is missing, ask the user to
  install it and open it once. Do not try to download it yourself.
- Tell the user what `ASSUME_YES` answers for them: it lets the installer install Go, Node.js and
  Python with winget (Windows) or Homebrew (macOS), and continue when their app build is newer
  than the project has recorded. If they want to be asked instead, leave it unset and let them
  run the command themselves in a terminal.
- On a Mac with no Apple signing certificate the installer builds with a basic signature (set
  `CODEX_SUBSCRIPTION_ROUTER_ALLOW_ADHOC_SIGNING=1` when nobody can be asked). Accounts, routing and
  usage work; Computer Use and Appshots may not. Say so.

When it finishes, the user opens **Codex Router** from the Start menu or Desktop (Windows) or
`~/Applications` (macOS), opens the profile menu at the bottom of the sidebar, and chooses
**Add another subscription**. Signing in to a subscription is the user's to do; never ask for or
handle their passwords or codes.

If anything fails, run the read-only diagnostics and show the user the output (it contains no
emails or tokens):

```powershell
irm https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/scripts/doctor.ps1 | iex
```

```sh
curl -fsSL https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/scripts/doctor.sh | /bin/bash
```

Rules that keep the user safe:

- Never modify, patch, or delete the official app or its data (`~/.codex`, the Store package).
- Never delete `~/.codex-mux` (the router's accounts and chat ownership) or its `backups`.
- Never set `--allow-untested-source` or loosen a check to get past an error. A message naming an
  anchor ("expected N ... found M") means the official app changed in a way the patch does not
  cover yet; stop, keep the printed `Source ... version` line, and open an issue.
- Do not run the installer elevated.

More: [README](README.md), [Windows notes](docs/WINDOWS.md), [troubleshooting](README.md#windows-troubleshooting).

## If you were asked to work on the repository

```sh
npm ci --ignore-scripts
go vet ./... && go test ./...
python3 -m unittest discover -s scripts -p "test_*.py"      # `python` on Windows
npm run check:js && npm run check:win
python3 scripts/check_release.py
bash scripts/test-install.sh                                  # macOS installer logic
powershell -File scripts/test-install.ps1                     # Windows installer logic
```

- **Anchors are exact and fail closed.** The patchers (`scripts/patch_app.py` for macOS,
  `scripts/patch_app_windows.py` for Windows) edit the official app only where a pattern matches
  exactly the expected number of times, and stop otherwise. Never weaken one to make a build pass;
  review the upstream change and update the pattern deliberately, with a test.
- **New official builds** arrive about daily. A watcher and a daily canary track them and keep one open
  issue per platform; if you were asked to handle one, follow [docs/MAINTAINING.md](docs/MAINTAINING.md)
  (`scripts/record_build.py` records a passing canary result; `scripts/probe_mac_asar.py` investigates a
  macOS failure without a Mac). A build the project has not recorded makes the installer ask
  (`--check-source` reports it with exit code 3); only a person promotes a build to *tested*.
- **Claims must match evidence.** What was verified, and what was not, is recorded in
  [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md); the website and README repeat only that.
- Releases are source-only. Never commit an app bundle, `.exe`, signing material, account data or
  a token.
