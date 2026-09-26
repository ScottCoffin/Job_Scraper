#!/usr/bin/env bash
# Merge upstream code improvements into this fork and push, keeping the fork's
# own scraped data and personal config.
#
# Used by .github/workflows/sync_upstream.yml, and safe to run by hand from a
# clone of your fork (e.g. to unstick a failed sync):
#
#   bash scripts/sync-upstream.sh
#
# What it does:
#   - git merge upstream/main -X ours: code merges in; content conflicts keep
#     this fork's side.
#   - output/ is reset to exactly this fork's tree. Scraped data is per-repo,
#     so upstream's demo data never leaks in — including one-sided upstream
#     edits and modify/delete conflicts, which neither -X ours nor merge=ours
#     handle.
#   - This script and sync_upstream.yml also stay exactly as this fork has
#     them, so an upstream edit can never break future syncs.
#   - config.json / scoring_profile.json are restored from a pre-merge backup.
#   - Any remaining conflict outside output/ is a real code conflict: the
#     script stops without committing or pushing.
#
# Why merge and not rebase: upstream once tracked and later deleted
# config.json. A rebase replays that delete and wipes the fork's config.
#
# Environment:
#   UPSTREAM_URL  upstream repo (default: ScottCoffin/Job_Scraper)

set -euo pipefail

UPSTREAM_URL="${UPSTREAM_URL:-https://github.com/ScottCoffin/Job_Scraper.git}"

err() {
  if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::error::$*"; else echo "Error: $*" >&2; fi
}

cd "$(git rev-parse --show-toplevel)"

# Resetting output/ below would discard uncommitted local changes.
if ! git diff --quiet || ! git diff --cached --quiet; then
  err "Working tree has uncommitted changes. Commit or stash them first."
  exit 1
fi

branch=$(git symbolic-ref --short HEAD)

# Define the "ours" merge driver referenced by .gitattributes; git has no
# built-in driver by that name, so without this the attribute is ignored.
git config merge.ours.driver true

if git remote get-url upstream >/dev/null 2>&1; then
  git remote set-url upstream "$UPSTREAM_URL"
else
  git remote add upstream "$UPSTREAM_URL"
fi
git fetch upstream main

backup=$(mktemp -d)
trap 'rm -rf "$backup"' EXIT
for f in config.json scoring_profile.json; do
  [ -f "$f" ] && cp "$f" "$backup/$f"
done

SYNC_MSG="chore: sync from upstream [$(date -u '+%Y-%m-%d')]"

# Merge without committing, so output/ and personal files can be put back to
# this fork's state first. A non-zero exit usually means file-level
# (modify/delete) conflicts, handled below.
if ! git merge upstream/main --no-commit --no-ff -X ours; then
  if ! git rev-parse -q --verify MERGE_HEAD >/dev/null; then
    err "git merge failed before starting; see output above."
    exit 1
  fi
  echo "Merge had conflicts; resolving output/ and personal files in favour of this fork..."
fi

if ! git rev-parse -q --verify MERGE_HEAD >/dev/null; then
  echo "Already up to date with upstream."
  exit 0
fi

# Reset fork-owned paths to exactly this fork's tree, dropping every upstream
# change (edits, additions, deletions, unresolved conflicts):
#   - output/ is per-repo scraped data.
#   - The sync machinery itself: a broken upstream edit here would stop every
#     future sync (upstream once added an invalid `workflows: write`
#     permission, which makes GitHub reject the whole workflow file).
for path in output .github/workflows/sync_upstream.yml scripts/sync-upstream.sh; do
  git rm -r -q -f --ignore-unmatch -- "$path"
  if git cat-file -e "HEAD:$path" 2>/dev/null; then
    git checkout HEAD -- "$path"
  fi
done

for f in config.json scoring_profile.json; do
  if [ -f "$backup/$f" ]; then
    cp "$backup/$f" "$f"
    git add -f "$f"
  fi
done

# Anything still unmerged is a real code conflict: stop rather than push a
# half-resolved merge.
unmerged=$(git diff --name-only --diff-filter=U)
if [ -n "$unmerged" ]; then
  err "Unexpected conflicts outside output/ — resolve manually, then commit and push:"
  echo "$unmerged"
  exit 1
fi

git commit --no-edit -q -m "$SYNC_MSG"

# GitHub refuses pushes that change .github/workflows/ unless the credential
# has workflow permission, which the Actions GITHUB_TOKEN can never have.
if ! push_out=$(git push origin "HEAD:$branch" 2>&1); then
  echo "$push_out"
  if grep -q "without \`workflows\` permission" <<<"$push_out"; then
    err "Push refused: this sync changes files in .github/workflows/, which the default Actions token cannot push. Add a SYNC_TOKEN secret (see README → 'Staying up to date with upstream'), or run 'bash scripts/sync-upstream.sh' from a local clone."
  fi
  exit 1
fi
echo "$push_out"
echo "✅ Synced successfully."
