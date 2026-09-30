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
GitHub Pages is now the primary website. The earlier Sites snapshot is not
automatically synchronized with Pages changes.

## Publishing

Public website: https://mvnshi.github.io/codex-subscription-router/

Pages uses native branch publishing from `gh-pages`, which was verified live on
2026-09-30. After committing and pushing website changes to `main`, run
`bash scripts/publish_website.sh` from the repository. It exports committed
website source and pushes it normally, without overwriting remote history.

The Website workflow can run the same export automatically when GitHub Actions
is available. GitHub's billing lock currently blocks that workflow; local
publishing and Pages' native branch builds still work and were verified.

The earlier Sites preview is owner-private at
https://codex-subscription-router.brevangblminor.chatgpt.site. Visitors should use
the public GitHub Pages URL, which requires no OpenAI account.

## Search visibility

The public landing page targets ChatGPT subscription router, multiple ChatGPT
accounts, subscription usage routing, and conversation failover with visible,
relevant content. The engineering article explains the implementation and links
to its evidence. Both pages have distinct titles/descriptions, canonical URLs,
Open Graph/X text metadata, and JSON-LD. No fabricated reviews or ratings are
included. All primary content is present in the HTML without JavaScript.

`sitemap.xml` lists the two public canonical pages. Keep `lastmod` accurate when
content changes. `404.html` is explicitly noindex; the normal pages permit
indexing. The font is self-hosted, assets use relative paths, and there is no
analytics or external runtime script.

A robots file controls crawling only at the origin root. On GitHub project
Pages, `website/robots.txt` is served below `/codex-subscription-router/` and is
not the root crawler policy; it is included for root-domain deployments. Root
crawler policy is controlled by `mvnshi.github.io/robots.txt`, when present.
Do not claim the project-level file controls Googlebot on the entire origin.

After publishing, the owner can add the URL-prefix property
`https://mvnshi.github.io/codex-subscription-router/` in Google Search Console,
verify it with Google's HTML file or meta tag, submit `sitemap.xml`, and request
indexing of the landing page and engineering article. Verification tokens must
come from the owner's Console; do not invent them. Search Console is not yet
connected or submitted by this repository. Indexing and ranking are Google's
decision, and adding metadata is not a guarantee of position or rich results.

Use the public repository's website field and homepage README link as discovery
paths. An owner-added link from munshi.nyc would be another useful, relevant
reference. Original-project attribution remains intact.
