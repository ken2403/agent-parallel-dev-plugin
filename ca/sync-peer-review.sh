#!/usr/bin/env bash
# Ship self-contained peer review launchers and the canonical ca review standards.
set -euo pipefail
CA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$CA_ROOT" "${1:-sync}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
mode = sys.argv[2]
if mode not in {"sync", "--check"}:
    raise SystemExit("usage: ca/sync-peer-review.sh [--check]")
claude = root / "claude/skills/ask-codex-review"
codex = root / "codex/skills/ca-ask-claude-review"
standards = root / "claude/skills/code-review"
outputs = {}
for skill in (claude, codex):
    outputs[skill / "references/standards.md"] = (standards / "SKILL.md").read_bytes().replace(
        b"references/", b"standards/")
    for source in sorted((standards / "references").glob("*.md")):
        outputs[skill / "references/standards" / source.name] = source.read_bytes()
for name in ("scripts/peer-review.py", "references/reviewer.md", "references/launcher.md"):
    outputs[codex / name] = (claude / name).read_bytes()
for path, content in outputs.items():
    if mode == "--check":
        if not path.is_file() or path.read_bytes() != content:
            raise SystemExit(f"stale peer-review copy: {path}; run bash ca/sync-peer-review.sh")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
print("ca peer-review copies are current")
PY
