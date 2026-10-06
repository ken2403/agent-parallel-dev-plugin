#!/usr/bin/env bash
# Fetch a PR's head commit into FETCH_HEAD for review, nothing else.
#
# review-pr pre-approves this script in allowed-tools instead of granting
# `Bash(git fetch origin pull/*/head)`: a `*` in a permission pattern also
# matches extra refspecs and flags, so the git grant would let hostile PR text
# smuggle ref writes into an unprompted fetch. This wrapper takes exactly one
# argument, a PR number, and builds the one refspec itself.
#
# Usage: fetch-pr-head.sh <pr-number>
set -euo pipefail

if [ "$#" -ne 1 ] || ! [[ "$1" =~ ^[0-9]+$ ]]; then
  echo "usage: fetch-pr-head.sh <pr-number>" >&2
  exit 2
fi

git fetch --no-tags origin "pull/$1/head"
