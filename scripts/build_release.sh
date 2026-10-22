#!/usr/bin/env bash
# Build a clean server package from the committed code only (no local data, caches,
# databases, secrets or tests):  bash scripts/build_release.sh  ->  dist/vertex1-server-<commit>.tar.gz
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "commit your changes first"; exit 1; }
REV=$(git rev-parse --short HEAD)
NAME=vertex1-server-$REV
mkdir -p dist && rm -rf "dist/$NAME" && mkdir "dist/$NAME"
git archive HEAD | tar -x -C "dist/$NAME"
rm -rf "dist/$NAME/tests" "dist/$NAME/scripts" "dist/$NAME/.gitignore" "dist/$NAME/requirements.txt" "dist/$NAME/donna/requirements.txt"
# refuse to ship anything that looks like a secret
if grep -rnEI '(sk-[A-Za-z0-9]{20,}|xox[bp]-[0-9A-Za-z-]{10,}|discord(app)?\.com/api/webhooks/[0-9]{5,}|[0-9]{8,10}:AA[0-9A-Za-z_-]{30,}|BEGIN [A-Z ]*PRIVATE KEY)' "dist/$NAME"; then
  echo "secret-like string found - aborting"; exit 1
fi
echo "$REV" > "dist/$NAME/RELEASE"
tar -C dist -czf "dist/$NAME.tar.gz" "$NAME"
(cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
rm -rf "dist/$NAME"
echo "built dist/$NAME.tar.gz"
