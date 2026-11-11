#!/usr/bin/env bash
# Render the cask template for a release and push it to the Homebrew tap.
# Usage: bump-homebrew-cask.sh <version> <asset file>
# Env: HOMEBREW_TAP_TOKEN (required unless HOMEBREW_TAP_REMOTE is set), HOMEBREW_TAP_REMOTE (override remote for dry runs).
set -euo pipefail

cask=unity-asset-library
version=${1:?usage: bump-homebrew-cask.sh <version> <asset file>}
asset=${2:?usage: bump-homebrew-cask.sh <version> <asset file>}

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "bump-homebrew-cask: invalid stable version '$version'" >&2
  exit 1
fi
if [[ ! -f "$asset" ]]; then
  echo "bump-homebrew-cask: asset not found: $asset" >&2
  exit 1
fi

if [[ -n "${HOMEBREW_TAP_REMOTE:-}" ]]; then
  remote=$HOMEBREW_TAP_REMOTE
elif [[ -n "${HOMEBREW_TAP_TOKEN:-}" ]]; then
  remote="https://x-access-token:${HOMEBREW_TAP_TOKEN}@github.com/ZeeMRx0/homebrew-tap.git"
else
  echo "bump-homebrew-cask: HOMEBREW_TAP_TOKEN is empty. Add it as a repository secret." >&2
  exit 1
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
template="$root/packaging/homebrew/$cask.rb"
sha256=$(shasum -a 256 "$asset" | cut -d' ' -f1)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Quiet output so the token in the remote URL is never echoed.
git clone --quiet "$remote" "$work/tap" >/dev/null 2>&1 || {
  echo "bump-homebrew-cask: cannot clone the tap" >&2
  exit 1
}
cd "$work/tap"

mkdir -p Casks
sed -e "s/__VERSION__/$version/g" -e "s/__SHA256__/$sha256/g" "$template" > "Casks/$cask.rb"

if [[ -z "$(git status --porcelain -- "Casks/$cask.rb")" ]]; then
  echo "bump-homebrew-cask: $cask $version already current"
  exit 0
fi

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add -- "Casks/$cask.rb"
git commit --quiet -m "$cask $version"

branch=$(git rev-parse --abbrev-ref HEAD)
for attempt in 1 2 3; do
  if git push --quiet origin "HEAD:$branch" >/dev/null 2>&1; then
    echo "bump-homebrew-cask: pushed $cask $version"
    exit 0
  fi
  echo "bump-homebrew-cask: push rejected (attempt $attempt of 3), rebasing" >&2
  git pull --quiet --rebase origin "$branch" >/dev/null 2>&1 || {
    echo "bump-homebrew-cask: rebase failed" >&2
    exit 1
  }
done
echo "bump-homebrew-cask: push failed after 3 attempts" >&2
exit 1
