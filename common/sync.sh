#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: common/sync.sh [--check]

Generate duplicated plugin files from common/src. In --check mode, fail if any
generated destination is stale or missing.
EOF
}

CHECK=0
if [ "${1:-}" = "--check" ]; then
  CHECK=1
  shift
fi
[ "$#" -eq 0 ] || { usage; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_ROOT="${COMMON_ROOT:-$SCRIPT_DIR}"
REPO_ROOT="${REPO_ROOT:-$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel 2>/dev/null || pwd)}"
MANIFEST="$COMMON_ROOT/manifest.tsv"

die() {
  echo "common/sync.sh: $*" >&2
  exit 1
}

rel() {
  case "$1" in
    "$REPO_ROOT"/*) printf '%s\n' "${1#"$REPO_ROOT"/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

plugin_for_dest() {
  case "$1" in
    ha/*) echo "ha" ;;
    sa/*) echo "sa" ;;
    ca/claude/*) echo "ca" ;;
    *) die "cannot infer plugin for destination '$1'" ;;
  esac
}

skill_for_src() {
  case "$1" in
    */skills/*/SKILL.md)
      local tail="${1#*/skills/}"
      echo "${tail%%/*}"
      ;;
    *) echo "" ;;
  esac
}

upper_key() {
  printf '%s' "$1" | tr '[:lower:]-' '[:upper:]_'
}

load_vars() {
  local plugin="$1" vars_file="$COMMON_ROOT/plugins/$plugin/vars"
  VAR_KEYS=()
  VAR_VALUES=()
  [ -f "$vars_file" ] || die "missing vars file '$vars_file'"

  local line key val lineno=0
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    [ -z "$line" ] && continue
    case "$line" in \#*) continue ;; esac
    [[ "$line" =~ ^[A-Z_][A-Z0-9_]*= ]] || die "$vars_file:$lineno: invalid vars line; expected KEY=value"
    key="${line%%=*}"
    val="${line#*=}"
    val="${val//\\n/$'\n'}"
    VAR_KEYS+=("$key")
    VAR_VALUES+=("$val")
  done < "$vars_file"
}

lookup_var() {
  local key="$1" i
  for ((i = 0; i < ${#VAR_KEYS[@]}; i++)); do
    if [ "${VAR_KEYS[$i]}" = "$key" ]; then
      printf '%s' "${VAR_VALUES[$i]}"
      return 0
    fi
  done
  return 1
}

list_contains() {
  local needle="$1" item
  shift
  for item in "$@"; do
    [ "$item" = "$needle" ] && return 0
  done
  return 1
}

substitute_vars() {
  local content="$1" src="$2" skill="$3" token key skill_key value
  while [[ "$content" =~ @@([A-Z_][A-Z0-9_]*)@@ ]]; do
    token="${BASH_REMATCH[0]}"
    key="${BASH_REMATCH[1]}"
    if [ "$key" = "DESCRIPTION" ] && [ -n "$skill" ]; then
      skill_key="$(upper_key "$skill")_DESCRIPTION"
      value="$(lookup_var "$skill_key")" || die "unknown variable '@@$key@@' in '$src' (also tried '$skill_key')"
      content="${content//$token/$value}"
    else
      value="$(lookup_var "$key")" || die "unknown variable '@@$key@@' in '$src'"
      content="${content//$token/$value}"
    fi
  done
  printf '%s' "$content"
}

render() {
  local class="$1" src_rel="$2" dest_rel="$3"
  local src="$COMMON_ROOT/$src_rel"
  local plugin skill content marker name frag frag_rel frag_content key

  [ -f "$src" ] || die "missing source '$src_rel'"
  plugin="$(plugin_for_dest "$dest_rel")"
  skill="$(skill_for_src "$src_rel")"
  load_vars "$plugin"
  content="$(cat "$src")"

  while [[ "$content" =~ @@FRAGMENT:([A-Za-z0-9_-]+)@@ ]]; do
    marker="${BASH_REMATCH[0]}"
    name="${BASH_REMATCH[1]}"
    [ -n "$skill" ] || die "fragment '$name' used outside a skill template in '$src_rel'"
    frag_rel="plugins/$plugin/fragments/$skill/$name.md"
    frag="$COMMON_ROOT/$frag_rel"
    [ -f "$frag" ] || die "missing fragment '$frag_rel' for source '$src_rel'"
    frag_content="$(cat "$frag")"
    [[ "$frag_content" != *"@@FRAGMENT:"* ]] || die "nested fragment marker in '$frag_rel'"
    key="$plugin/$skill/$name"
    USED_FRAGMENTS+=("$key")
    content="${content//$marker/$frag_content}"
  done

  RENDERED_CONTENT="$(substitute_vars "$content" "$src_rel" "$skill")"
}

mode_for_class() {
  case "$1" in
    script) echo 755 ;;
    reference|skill) echo 644 ;;
    *) die "unknown manifest class '$1'" ;;
  esac
}

add_script_banner() {
  local content="$1" src_rel="$2"
  local banner="# Generated from common/$src_rel; edit common/src and run common/sync.sh."
  awk -v banner="$banner" 'NR == 1 { print; print banner; next } { print }' <<< "$content"
}

check_manifest_coverage() {
  [ "${COMMON_SYNC_SKIP_COVERAGE:-0}" = "1" ] && return 0
  COVERAGE_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/common-sync-coverage.XXXXXX")"
  local generated_file="$COVERAGE_TMPDIR/generated"
  local manifest_file="$COVERAGE_TMPDIR/paths"
  local exclusions_file="$COMMON_ROOT/exclusions.tsv"
  local excluded_file="$COVERAGE_TMPDIR/excluded"
  local candidates_file="$COVERAGE_TMPDIR/candidates"

  : > "$manifest_file"
  printf '%s\n' "${MANIFEST_DESTS[@]}" | sort -u > "$manifest_file"

  if [ -f "$exclusions_file" ]; then
    awk -F '\t' '
      NF == 0 || /^$/ || /^#/ { next }
      NF != 2 { printf "%s:%d: expected path<TAB>reason\n", FILENAME, NR > "/dev/stderr"; exit 1 }
      $1 == "" || $2 == "" { printf "%s:%d: path and reason are required\n", FILENAME, NR > "/dev/stderr"; exit 1 }
      { print $1 }
    ' "$exclusions_file" | sort -u > "$excluded_file"
  else
    : > "$excluded_file"
  fi

  while IFS= read -r excluded_path; do
    [ -z "$excluded_path" ] && continue
    git -C "$REPO_ROOT" ls-files --error-unmatch "$excluded_path" >/dev/null 2>&1 ||
      die "exclusions.tsv references untracked or missing path '$excluded_path'"
  done < "$excluded_file"

  git -C "$REPO_ROOT" ls-files ha sa ca |
    awk '
      /\/scripts\/(detect-base-branch|attach-or-create-worktree|merge-check|clean|new-worktree|fetch-pr-head)\.sh$/ { print; next }
      /\/hooks\/guard-protected\.sh$/ { print; next }
      /\/skills\/code-review\/references\/(code-quality|consistency|security|test-rigor)\.md$/ { print; next }
      /\/skills\/[^\/]+\/references\/review-contract\.md$/ { print; next }
      /\/skills\/(clean-worktrees|merge-pr|resolve-conflicts|review-pr)\/SKILL\.md$/ { print; next }
      /\/skills\/code-review\/SKILL\.md$/ { print; next }
      /^ca\/codex\// { print; next }
    ' | sort -u > "$candidates_file"

  comm -23 "$excluded_file" "$candidates_file" > "$generated_file"
  if [ -s "$generated_file" ]; then
    sed 's/^/stale or unnecessary exclusion: /' "$generated_file" >&2
    die "remove stale exclusions or update common/sync.sh duplicate detection"
  fi

  comm -23 "$candidates_file" <(cat "$manifest_file" "$excluded_file" | sort -u) > "$generated_file"
  if [ -s "$generated_file" ]; then
    sed 's/^/unclassified duplicated destination: /' "$generated_file" >&2
    die "classify each path in common/manifest.tsv or common/exclusions.tsv with a reason"
  fi

  comm -23 "$manifest_file" "$candidates_file" > "$generated_file"
  if [ -s "$generated_file" ]; then
    sed 's/^/manifest destination is not a known duplicated path: /' "$generated_file" >&2
    die "remove incorrect manifest rows or update common/sync.sh duplicate detection"
  fi
}

COVERAGE_TMPDIR=""
trap '[ -n "$COVERAGE_TMPDIR" ] && rm -rf "$COVERAGE_TMPDIR"' EXIT

[ -f "$MANIFEST" ] || die "missing manifest '$MANIFEST'"

SEEN_DESTS=()
USED_FRAGMENTS=()
VAR_KEYS=()
VAR_VALUES=()
MANIFEST_DESTS=()
LINE_NO=0

while IFS= read -r line || [ -n "$line" ]; do
  LINE_NO=$((LINE_NO + 1))
  [ -z "$line" ] && continue
  case "$line" in \#*) continue ;; esac
  [[ "$line" != *" "* ]] || die "manifest.tsv:$LINE_NO: spaces are not allowed; use exact tabs"
  IFS=$'\t' read -r class src_rel dest_rel extra <<< "$line"
  [ -z "${extra:-}" ] || die "manifest.tsv:$LINE_NO: expected exactly 3 tab-separated columns"
  [ -n "${class:-}" ] && [ -n "${src_rel:-}" ] && [ -n "${dest_rel:-}" ] || die "manifest.tsv:$LINE_NO: expected class, source, destination"
  mode_for_class "$class" >/dev/null
  ! list_contains "$dest_rel" "${SEEN_DESTS[@]+"${SEEN_DESTS[@]}"}" || die "manifest.tsv:$LINE_NO: duplicate destination '$dest_rel'"
  SEEN_DESTS+=("$dest_rel")
  MANIFEST_DESTS+=("$dest_rel")

  render "$class" "$src_rel" "$dest_rel"
  rendered="$RENDERED_CONTENT"
  if [ "$class" = "script" ]; then
    rendered="$(add_script_banner "$rendered" "$src_rel")"
  fi
  dest="$REPO_ROOT/$dest_rel"
  mode="$(mode_for_class "$class")"

  if [ "$CHECK" -eq 1 ]; then
    [ -f "$dest" ] || die "missing destination '$dest_rel' generated from '$src_rel'; run: bash common/sync.sh"
    tmp="$(mktemp)"
    printf '%s\n' "$rendered" > "$tmp"
    if ! cmp -s "$tmp" "$dest"; then
      rm -f "$tmp"
      die "stale generated file '$dest_rel'; edit '$src_rel' or its plugin fragments/vars, then run: bash common/sync.sh"
    fi
    rm -f "$tmp"
  else
    mkdir -p "$(dirname "$dest")"
    tmp="$(mktemp)"
    printf '%s\n' "$rendered" > "$tmp"
    install -m "$mode" "$tmp" "$dest"
    rm -f "$tmp"
  fi
done < "$MANIFEST"

for frag in "$COMMON_ROOT"/plugins/*/fragments/*/*.md; do
  [ -e "$frag" ] || continue
  rel_frag="$(rel "$frag")"
  rel_frag="${rel_frag#common/}"
  plugin="${rel_frag#plugins/}"
  plugin="${plugin%%/*}"
  rest="${rel_frag#plugins/$plugin/fragments/}"
  skill="${rest%%/*}"
  name="${rest#*/}"
  name="${name%.md}"
  list_contains "$plugin/$skill/$name" "${USED_FRAGMENTS[@]+"${USED_FRAGMENTS[@]}"}" || die "unused fragment '$rel_frag'"
done

check_manifest_coverage

if [ "$CHECK" -eq 1 ]; then
  echo "common/sync.sh: generated files are up to date"
else
  echo "common/sync.sh: generated files refreshed"
fi
