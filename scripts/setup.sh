#!/usr/bin/env bash
# setup.sh — one-command first-time setup for a Job_Scraper fork
#
# What it does (idempotent — safe to re-run):
#   1. Checks prerequisites (gh CLI installed + authenticated)
#   2. Enables GitHub Actions with read+write workflow permissions
#   3. Enables GitHub Pages (main branch, / root)
#   4. Sets ENABLE_DATA_COMMITS=true (the variable that makes scrapers save results)
#   5. Optionally sets Pushover notification secrets
#   6. Optionally sets Anthropic API key for AI fit-scoring
#   7. Optionally triggers a first-time backfill on each watcher, one at a time
#
# Requirements:
#   gh CLI (https://cli.github.com) installed and authenticated
#   Run from the root of your cloned fork:  bash scripts/setup.sh

set -euo pipefail

# ── Terminal helpers ───────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC}  $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
info() { echo -e "   $*"; }
err()  { echo -e "${RED}✗${NC}  $*"; }
step() { echo -e "\n${CYAN}──${NC} $*"; }
ask()  { local prompt="$1"; local REPLY; read -r -p "   ${prompt} " REPLY; echo "$REPLY"; }

echo ""
echo "╔═════════════════════════════════════════════════════╗"
echo "║       Job Scraper — First-Time Setup Script         ║"
echo "╚═════════════════════════════════════════════════════╝"
echo ""

# ── 1. Prerequisites ───────────────────────────────────────────────────────────
step "Checking prerequisites"

if ! command -v gh &>/dev/null; then
  err "GitHub CLI (gh) is not installed."
  info "Install from https://cli.github.com, then run:  gh auth login"
  exit 1
fi
ok "GitHub CLI: $(gh --version | head -1)"

if ! gh auth status &>/dev/null 2>&1; then
  err "Not authenticated. Run:  gh auth login"
  exit 1
fi
GH_USER=$(gh api user -q .login 2>/dev/null)
ok "Authenticated as: $GH_USER"

# ── 2. Detect repo ─────────────────────────────────────────────────────────────
step "Detecting repository"

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)
if [ -z "$REPO" ]; then
  err "Could not detect GitHub repo. Run this script from inside your cloned fork."
  exit 1
fi
ok "Target repo: $REPO"

OWNER=$(echo "$REPO" | cut -d/ -f1)
REPONAME=$(echo "$REPO" | cut -d/ -f2)

# Warn if running against the upstream template (not a fork)
PARENT=$(gh repo view --json parent -q '.parent.nameWithOwner // ""' 2>/dev/null || true)
if [ -z "$PARENT" ]; then
  warn "This repo has no parent — if you haven't forked yet, do so first."
fi

# ── 3. Config files ─────────────────────────────────────────────────────────────
step "Checking config files"

if [ -f "config.json" ]; then
  ok "config.json found."
else
  warn "config.json not found."
  info "You must create this before scrapers will return useful results."
  info "Option A: Copy config.example.json → config.json and fill in your keywords/locations."
  info "Option B: Use docs/cv-to-config-prompt.md with an LLM to generate one from your CV."
  info "You can do this in the GitHub web UI (Add file → config.json) or locally."
fi

if [ -f "scoring_profile.json" ]; then
  ok "scoring_profile.json found."
else
  info "scoring_profile.json not found (optional — only needed for AI triage)."
fi

# ── 4. Actions permissions ──────────────────────────────────────────────────────
step "Configuring Actions permissions"

# Enable Actions
if gh api "repos/$REPO/actions/permissions" \
    --method PUT \
    --field enabled=true \
    --field allowed_actions=all \
    --silent 2>/dev/null; then
  ok "GitHub Actions enabled."
else
  warn "Could not enable Actions via API (may already be enabled)."
fi

# Set workflow permissions to read+write
if gh api "repos/$REPO/actions/permissions/workflow" \
    --method PUT \
    --field default_workflow_permissions=write \
    --field can_approve_pull_request_reviews=false \
    --silent 2>/dev/null; then
  ok "Workflow permissions set to read+write."
else
  warn "Could not set workflow permissions — set manually: Settings → Actions → General → Workflow permissions → Read and write."
fi

# ── 5. Required variable ────────────────────────────────────────────────────────
step "Setting required variables"

CURRENT_VAL=$(gh api "repos/$REPO/actions/variables/ENABLE_DATA_COMMITS" -q .value 2>/dev/null || true)
if [ "$CURRENT_VAL" = "true" ]; then
  ok "ENABLE_DATA_COMMITS=true already set."
else
  gh variable set ENABLE_DATA_COMMITS --body "true" 2>/dev/null \
    && ok "ENABLE_DATA_COMMITS=true set." \
    || { err "Failed to set ENABLE_DATA_COMMITS."; info "Set manually: Settings → Secrets and variables → Actions → Variables tab → Add ENABLE_DATA_COMMITS = true"; }
fi

# ── 6. GitHub Pages ─────────────────────────────────────────────────────────────
step "Enabling GitHub Pages"

PAGES_STATUS=$(gh api "repos/$REPO/pages" -q .status 2>/dev/null || true)
if [ -n "$PAGES_STATUS" ]; then
  PAGES_URL=$(gh api "repos/$REPO/pages" -q .html_url 2>/dev/null || true)
  ok "GitHub Pages already active: $PAGES_URL"
else
  if gh api "repos/$REPO/pages" \
      --method POST \
      --field source[branch]=main \
      --field source[path]=/ \
      --silent 2>/dev/null; then
    ok "GitHub Pages enabled (main branch, / root)."
    info "Dashboard will be live in ~1 minute at: https://${OWNER}.github.io/${REPONAME}/triage.html"
  else
    warn "Could not enable Pages automatically."
    info "Set manually: Settings → Pages → Deploy from branch → main / (root)"
  fi
fi

# ── 7. Pushover notifications (optional) ────────────────────────────────────────
step "Pushover phone notifications (optional)"

EXISTING_SECRETS=$(gh secret list --json name -q '.[].name' 2>/dev/null || true)

if echo "$EXISTING_SECRETS" | grep -q "^PUSHOVER_TOKEN$"; then
  ok "PUSHOVER_TOKEN already set."
else
  info "Pushover sends a push notification the moment a high-fit role appears."
  info "One-time ~\$5 app (iOS/Android); API is free. See README Step 6."
  TOKEN=$(ask "Pushover API Token (press Enter to skip):")
  if [ -n "$TOKEN" ]; then
    printf '%s' "$TOKEN" | gh secret set PUSHOVER_TOKEN \
      && ok "PUSHOVER_TOKEN set." \
      || warn "Failed to set PUSHOVER_TOKEN."
  else
    info "Skipped. Add later: Settings → Secrets and variables → Actions → New secret."
  fi
fi

if echo "$EXISTING_SECRETS" | grep -q "^PUSHOVER_USER$"; then
  ok "PUSHOVER_USER already set."
else
  if ! echo "$EXISTING_SECRETS" | grep -q "^PUSHOVER_TOKEN$"; then
    USER_KEY=$(ask "Pushover User Key (press Enter to skip):")
    if [ -n "$USER_KEY" ]; then
      printf '%s' "$USER_KEY" | gh secret set PUSHOVER_USER \
        && ok "PUSHOVER_USER set." \
        || warn "Failed to set PUSHOVER_USER."
    fi
  fi
fi

# ── 8. AI fit-scoring via Claude API (optional) ──────────────────────────────────
step "AI fit-scoring via Claude API (optional)"

if echo "$EXISTING_SECRETS" | grep -q "^ANTHROPIC_API_KEY$"; then
  ok "ANTHROPIC_API_KEY already set."
else
  info "The triage agent scores each job against your résumé using Claude."
  info "Costs ~pennies/run. Requires Anthropic API key (anthropic.com/api)."
  info "Leave blank to skip — the triage.yml workflow is disabled by default."
  API_KEY=$(ask "Anthropic API Key (press Enter to skip):")
  if [ -n "$API_KEY" ]; then
    printf '%s' "$API_KEY" | gh secret set ANTHROPIC_API_KEY \
      && ok "ANTHROPIC_API_KEY set." \
      || warn "Failed to set ANTHROPIC_API_KEY."
    info "Also set CANDIDATE_PROFILE and CANDIDATE_RESUME secrets:"
    info "  gh secret set CANDIDATE_PROFILE   # paste your short profile, Ctrl+D when done"
    info "  gh secret set CANDIDATE_RESUME    # paste your résumé text, Ctrl+D when done"
    info "Then enable the triage.yml workflow: Actions → Nightly Job Triage → Enable workflow"
  else
    info "Skipped. triage.yml stays disabled — no AI scoring, no cost."
  fi
fi

# ── 9. First-time backfill (optional) ───────────────────────────────────────────
step "First-time backfill (optional)"

info "A backfill seeds your dataset with 30–61 days of historical listings."
info "Watchers run one at a time: they share a lock, and GitHub cancels runs that"
info "queue up behind it. Keep this window open until they finish (a few minutes each)."
info "LinkedIn uses the 'LinkedIn Backfill (Parallel)' workflow, started last; it needs"
info "your own search_terms.linkedin in config.json (README → 'LinkedIn backfill')."
TRIGGER=$(ask "Run backfill now? [y/N]:")

# Trigger a workflow and wait for the run it started to finish.
run_and_wait() { # workflow file, then extra `gh workflow run` args
  local wf=$1; shift
  local before id=""
  # Only manually dispatched runs, so a scheduled run of the same workflow
  # starting at the same moment is never mistaken for ours.
  dispatched_runs() { gh run list -w "$wf" --event workflow_dispatch -L 20 --json databaseId -q '.[].databaseId' 2>/dev/null; }
  before=$(dispatched_runs || true)
  if ! gh workflow run "$wf" "$@" 2>/dev/null; then
    info "  – Skipped: $wf (not found or workflow disabled)"
    return 0
  fi
  for _ in $(seq 1 60); do
    sleep 5
    id=$(dispatched_runs | grep -vxF -f <(printf '%s\n' "$before") | head -1 || true)
    [ -n "$id" ] && break
  done
  if [ -z "$id" ]; then
    warn "  $wf: triggered, but its run didn't appear within 5 minutes; check the Actions tab."
    return 0
  fi
  info "  … $wf running (run $id)"
  if gh run watch "$id" --exit-status >/dev/null 2>&1; then
    ok "  $wf finished"
  else
    warn "  $wf didn't succeed; check the Actions tab."
  fi
}

if [[ "${TRIGGER,,}" =~ ^y ]]; then
  for wf in indeed_watch.yml ziprecruiter_watch.yml hiringcafe_watch.yml localgov_watch.yml scrape_jobs.yml; do
    run_and_wait "$wf" --field backfill=true
  done
  # These don't have a backfill toggle — a normal run is a full snapshot
  for wf in calcareers_watch.yml usajobs_watch.yml; do
    run_and_wait "$wf"
  done

  # LinkedIn: the parallel backfill takes about an hour, so start it and move on.
  if gh workflow run linkedin_backfill.yml 2>/dev/null; then
    ok "Started LinkedIn Backfill (Parallel). It takes about an hour; watch it in the Actions tab."
  else
    info "  – Skipped: linkedin_backfill.yml (not found or workflow disabled)"
  fi
else
  info "Skipped. To run later, one at a time: Actions → [Watcher] → Run workflow → check"
  info "'One-time backfill'. For LinkedIn: Actions → LinkedIn Backfill (Parallel) → Run workflow."
fi

# ── Summary ─────────────────────────────────────────────────────────────────────
echo ""
echo "╔═════════════════════════════════════════════════════╗"
echo "║               Setup complete!                       ║"
echo "╚═════════════════════════════════════════════════════╝"
echo ""
info "Dashboard: https://${OWNER}.github.io/${REPONAME}/triage.html"
info ""
info "Confirm everything is configured:"
info "  Actions → Validate Setup → Run workflow"
echo ""
