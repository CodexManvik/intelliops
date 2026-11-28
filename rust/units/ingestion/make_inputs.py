"""Typed edge-case inputs for compute_fingerprint (labels is dict[str, str])."""

import json
import random
from pathlib import Path

ATOMS = [
    "",
    "a",
    "Z",
    "service",
    "instance",
    "|",
    "=",
    "a|b",
    "k=v",
    "é",
    "ü",
    "İ",
    "ß",
    "📈",
    "￿",
    "﻿",
    "\t",
    "\n",
    " ",
    "10",
    "9",
    "aa",
    "A",
    "_",
    "~",
    "\u0000",
    "é",
]

rng = random.Random(1)


def word() -> str:
    return "".join(rng.choice(ATOMS) for _ in range(rng.randint(0, 4)))


lines = []
for _ in range(600):
    labels = {word(): word() for _ in range(rng.randint(0, 6))}
    lines.append(json.dumps({"fn": "compute_fingerprint", "args": [word(), word(), labels]}))
Path(__file__).with_name("inputs.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
