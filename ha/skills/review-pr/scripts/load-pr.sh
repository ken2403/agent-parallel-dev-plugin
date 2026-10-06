#!/usr/bin/env bash
# Generated from common/src/scripts/load-pr.sh; edit common/src and run common/sync.sh.
# Load a PR for review: its metadata, its diff, and its head commit
# (fetched into FETCH_HEAD and checked against the PR's headRefOid).
#
# review-pr pre-approves THIS script in allowed-tools instead of granting
# `Bash(gh pr view *)` / `Bash(gh pr diff *)` / `Bash(git fetch origin pull/*/head)`:
# a `*` in a permission pattern also matches extra flags, and the reviewed PR is
# untrusted text that may try to steer the model. With those grants it could get
# `gh pr view 1 --jq '$ENV.GH_TOKEN'` (prints secrets), `-R <data>.evil.example/o/r`
# (sends data to another host), or an extra fetch refspec (writes local refs) run
# without a prompt. This wrapper takes at most one argument, a PR number, and
# builds every command itself.
#
# Usage: load-pr.sh [pr-number]   (no number: the current branch's PR)
set -euo pipefail
export LC_ALL=C

usage() { echo "usage: load-pr.sh [pr-number]" >&2; exit 2; }
is_number() { [[ "$1" =~ ^[0-9]+$ ]]; }

[ "$#" -le 1 ] || usage
if [ "$#" -eq 1 ]; then
  is_number "$1" || usage
  n="$1"
else
  n="$(gh pr view --json number --jq .number)" || { echo "error: no PR for the current branch" >&2; exit 1; }
  is_number "$n" || { echo "error: no PR for the current branch" >&2; exit 1; }
fi

fields=number,title,body,url,headRefOid,headRefName,baseRefName,isDraft,additions,deletions,files,reviewDecision,statusCheckRollup
echo "== metadata (PR #$n) =="
gh pr view "$n" --json "$fields"

sha="$(gh pr view "$n" --json headRefOid --jq .headRefOid)"
echo
echo "== head =="
git fetch --quiet --no-tags --no-recurse-submodules origin "refs/pull/$n/head"
fetched="$(git rev-parse FETCH_HEAD)"
if [ "$fetched" != "$sha" ]; then
  echo "error: fetched $fetched but the PR head is $sha (pushed mid-review?) — rerun" >&2
  exit 1
fi
echo "headRefOid $sha fetched; read PR code with: git show $sha:<path>"

echo
echo "== diff =="
gh pr diff "$n"
