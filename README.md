# Router website

Static landing page for the maintained subscription-router fork. GitHub Pages
publishes this directory after website changes reach `main`; no frontend build
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

## Hosting status

The initial Sites preview is owner-private at
https://codex-subscription-router.brevangblminor.chatgpt.site.
The public GitHub Pages target is https://mvnshi.github.io/codex-subscription-router/.
Its initial deployment was blocked by a GitHub account billing lock on
2026-09-30. Once that account restriction is cleared, rerun the Website workflow;
no source changes are required.
