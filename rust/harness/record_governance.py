"""Record Python's fnmatch (as RBAC uses it) and export the author's prompts.

uv run python rust/harness/record_governance.py
"""

from __future__ import annotations

import json
import random
from fnmatch import fnmatchcase
from pathlib import Path

from services.governance.adapters import runbook_author
from services.governance.adapters.author_tools import ACTION_NOTES, TOOL_SCHEMAS

RUST = Path(__file__).resolve().parents[1]
ATOMS = ["a", "b", "z", ":", "/", ".", "-", "*", "?", "[", "]", "!", "^", "\\", "{", "é"]


def fnmatch_cases() -> list[list]:
    rng = random.Random(5)
    fixed = [
        ("playbook:restart-pod", "playbook:*"),
        ("situation:sit-1", "playbook:*"),
        ("anything/at all", "*"),
        ("playbook:a/b", "playbook:*"),
        ("a[b", "a[b"),
        ("a]", "[]a]]"),
        ("^", "[^]"),
        ("!", "[!]"),
        ("b", "[a-c]"),
        ("-", "[a-]"),
        ("b", "[!a]"),
        ("\\", "[\\]"),
        ("", ""),
        ("", "*"),
        ("Playbook:x", "playbook:*"),
    ]
    cases = [[name, pattern, fnmatchcase(name, pattern)] for name, pattern in fixed]
    for _ in range(3000):
        name = "".join(rng.choice(ATOMS) for _ in range(rng.randrange(6)))
        pattern = "".join(rng.choice(ATOMS) for _ in range(rng.randrange(6)))
        try:
            cases.append([name, pattern, fnmatchcase(name, pattern)])
        except Exception:  # noqa: BLE001, S112 - e.g. a bad character range
            continue
    return cases


def main() -> None:
    cases = fnmatch_cases()
    (RUST / "app-cases" / "fnmatch.json").write_text(
        json.dumps(cases, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    prompts = {
        "system": runbook_author._AGENT_SYSTEM_PROMPT,
        "nudge": runbook_author._NUDGE_MESSAGE,
        "action_notes": ACTION_NOTES,
        "tools": TOOL_SCHEMAS,
    }
    (RUST / "crates" / "governance" / "assets" / "author.json").write_text(
        json.dumps(prompts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(len(cases), "fnmatch cases,", sum(c[2] for c in cases), "match")


if __name__ == "__main__":
    main()
