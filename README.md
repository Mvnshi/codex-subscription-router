# Router website

Static landing page for the maintained subscription-router fork. GitHub Pages
publishes the `gh-pages` branch, exported from this directory; no frontend build
or runtime dependencies are required.

To preview locally, run `python3 -m http.server 4173 --directory website` from the
repository root, then open `http://localhost:4173`.

The routing demo uses illustrative account data only. It never calls the router,
signs in to accounts, or spends reset credits. The macOS and provisional Windows
instructions link to the maintained installer and compatibility notes.

The maintainer link points to https://munshi.nyc. There is no payment integration,
analytics, or browser storage. Manrope is self-hosted with its SIL Open Font License.

Edit `index.html`, `styles.css`, and `app.js` directly. Keep all assets relative so
the same page works at the repository's GitHub Pages subpath and on other static
hosts. Sites preview source is maintained in the workspace's `router-site` checkout;
when updating it, sync these public files into that checkout's `dist/` directory.

## Publishing

Public website: https://mvnshi.github.io/codex-subscription-router/

Pages uses native branch publishing from `gh-pages`, which was verified live on
2026-09-30. After committing and pushing website changes to `main`, run
`bash scripts/publish_website.sh` from the repository. It exports committed
website source and pushes it normally, without overwriting remote history.

The Website workflow can run the same export automatically when GitHub Actions
is available. GitHub's billing lock currently blocks that workflow; local
publishing and Pages' native branch builds still work and were verified.

The separate Sites preview is owner-private at
https://codex-subscription-router.brevangblminor.chatgpt.site. Visitors should use
the public GitHub Pages URL, which requires no OpenAI account.
