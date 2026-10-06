#!/usr/bin/env bash
# Print one issue of the current repo for /ha:plan. plan pre-approves THIS
# script instead of `Bash(gh issue view *)`: a `*` also matches a URL or
# `-R <host>/o/r` (requests to an attacker-chosen host) and `--jq '$ENV...'`
# (prints secrets), so the grant would run those unprompted. Number only.
#
# Usage: issue-view.sh <issue-number>
set -euo pipefail
export LC_ALL=C

if [ "$#" -ne 1 ] || ! [[ "$1" =~ ^[0-9]+$ ]]; then
  echo "usage: issue-view.sh <issue-number>" >&2
  exit 2
fi

gh issue view "$1" --json number,title,state,labels,body,comments
