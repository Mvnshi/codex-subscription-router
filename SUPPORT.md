# Getting help

- **It will not install, or something stopped working.** Run the read-only
  [doctor](README.md#when-something-goes-wrong) (it prints no emails, tokens or chats) and
  [open an issue](https://github.com/Mvnshi/codex-subscription-router/issues/new/choose) with its output.
- **A message you do not understand.** The [Windows troubleshooting table](README.md#windows-troubleshooting)
  explains every message the installer and patcher print.
- **The Codex or ChatGPT app updated and the router seems behind.** Run the install command again; it
  rebuilds from the new app and keeps your accounts. If it says the build is newer than the project has
  recorded, answer yes: the patch stops by itself if the app changed in a way it does not cover. The
  [open issues labelled `upstream-build`](https://github.com/Mvnshi/codex-subscription-router/labels/upstream-build)
  show where each platform stands.
- **A security problem** (credentials, signing, the local control service): see [SECURITY.md](SECURITY.md)
  and report it privately.

This is an unofficial project and is not supported by OpenAI.
