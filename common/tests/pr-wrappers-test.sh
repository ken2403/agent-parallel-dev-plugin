#!/usr/bin/env bash
# The review-pr / plan wrappers are the only Bash that those model-invocable
# skills pre-approve, so their argument check IS the security boundary. Run them
# against stubbed gh/git and assert: hostile arguments exit 2 before any command
# runs; a number produces exactly the expected argv; a head mismatch fails.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/pr-wrappers-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
pass=0
fail() { echo "not ok - $*" >&2; exit 1; }
ok() { echo "ok - $*"; pass=$((pass + 1)); }

mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'STUB'
#!/usr/bin/env bash
printf 'gh %s\n' "$*" >> "$CALLS"
case "$*" in
  "pr view --json number --jq .number") echo 42 ;;
  *"--json headRefOid --jq .headRefOid") echo "${HEAD_SHA:-abc123}" ;;
  "pr view "*) echo '{"number":1}' ;;
  "pr diff "*) echo 'diff --git a/x b/x' ;;
  "issue view "*) echo 'issue body' ;;
esac
STUB
cat > "$TMP/bin/git" <<'STUB'
#!/usr/bin/env bash
printf 'git %s\n' "$*" >> "$CALLS"
case "$1" in rev-parse) echo "${FETCHED_SHA:-abc123}" ;; esac
STUB
chmod +x "$TMP/bin/gh" "$TMP/bin/git"
export PATH="$TMP/bin:$PATH" CALLS="$TMP/calls"

run() { : > "$CALLS"; set +e; out="$("$@" 2>&1)"; status=$?; set -e; }

HOSTILE=('' '1 2' '71x' '-1' '+1' '1;id' '$(id)' '--upload-pack=x' $'1\n' $'1\n2'
  '１' 'https://evil.example/o/r/pull/1' '-R' "--jq=\$ENV")

check_wrapper() {
  local name="$1" script="$2"
  for arg in "${HOSTILE[@]}"; do
    run bash "$script" "$arg"
    [ "$status" -eq 2 ] || fail "$name rejects $(printf %q "$arg") (exit $status)"
    [ ! -s "$CALLS" ] || fail "$name ran a command for $(printf %q "$arg"): $(cat "$CALLS")"
  done
  run bash "$script" 1 2
  [ "$status" -eq 2 ] && [ ! -s "$CALLS" ] || fail "$name rejects two arguments"
  ok "$name rejects hostile arguments before running anything"
}

for copy in common/src/scripts/load-pr.sh sa/skills/review-pr/scripts/load-pr.sh ha/skills/review-pr/scripts/load-pr.sh; do
  script="$ROOT/$copy"
  check_wrapper "$copy" "$script"

  run bash "$script" 71
  [ "$status" -eq 0 ] || fail "$copy 71 succeeds: $out"
  expected="gh pr view 71 --json number,title,body,url,headRefOid,headRefName,baseRefName,isDraft,additions,deletions,files,reviewDecision,statusCheckRollup
gh pr view 71 --json headRefOid --jq .headRefOid
git fetch --quiet --no-tags --no-recurse-submodules origin refs/pull/71/head
git rev-parse FETCH_HEAD
gh pr diff 71"
  [ "$(cat "$CALLS")" = "$expected" ] || fail "$copy argv: $(cat "$CALLS")"
  ok "$copy builds exactly the expected commands"

  run bash "$script"
  [ "$status" -eq 0 ] && grep -q '^gh pr diff 42$' "$CALLS" || fail "$copy auto-detects the branch PR"
  ok "$copy auto-detects the current branch's PR"

  FETCHED_SHA=def456 run bash "$script" 71
  [ "$status" -eq 1 ] && ! grep -q 'pr diff' "$CALLS" || fail "$copy refuses a head mismatch"
  ok "$copy refuses a fetched head that is not headRefOid"
done

script="$ROOT/ha/skills/plan/scripts/issue-view.sh"
check_wrapper issue-view.sh "$script"
run bash "$script" 7
[ "$status" -eq 0 ] && [ "$(cat "$CALLS")" = "gh issue view 7 --comments" ] || fail "issue-view argv: $(cat "$CALLS")"
ok "issue-view.sh builds exactly the expected command"

echo "pr-wrappers-test: $pass tests passed"
