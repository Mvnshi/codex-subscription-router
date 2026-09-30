#!/usr/bin/env bash
# Publish the committed website without running a GitHub Actions build.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
if [[ -n "$(git status --porcelain -- website)" ]]; then
  echo 'Commit website changes before publishing.' >&2
  exit 1
fi
website_commit="$(git subtree split --prefix=website)"
git push origin "${website_commit}:refs/heads/gh-pages"
echo 'Website source published. GitHub Pages will build the gh-pages branch.'
