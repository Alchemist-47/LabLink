"""Check equipment suggestions against real Gemini.

Usage (PowerShell):
    $env:GEMINI_API_KEY = "<your key>"          # never commit it
    $env:GEMINI_MODEL = "gemini-3.5-flash-lite"  # optional
    python scripts/eval_ai.py

Each case lists equipment that must be suggested (in either tier) and equipment that
must not be. The script exits non-zero if any case fails.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lablink.ai import DEFAULT_MODEL, GEMINI, LabAssistant  # noqa: E402
from lablink.seed import EQUIPMENT  # noqa: E402

ALL = {e.id for e in EQUIPMENT}
CASES = [
    ("Build a desalination prototype for a coastal village", "Mechanical", {"eq_uvvis"}, set()),
    ("Building an LALR(1) compiler in C++ for our compilers course", "Computer Science", set(), ALL),
    ("A web app to track attendance with a PostgreSQL database and a data analysis dashboard",
     "Computer Science", set(), ALL),
    ("EMG-controlled prosthetic hand with printed finger linkages", "Electronics",
     {"eq_dso"}, {"eq_pcr", "eq_uvvis"}),
    ("Smart irrigation with soil moisture sensors on an ESP32", "Computer Science",
     {"eq_esp32"}, {"eq_pcr"}),
    ("Quadcopter with a printed frame that inspects rooftops using onboard computer vision",
     "Computer Science", {"eq_jetson"}, {"eq_pcr", "eq_uvvis"}),
    ("Train a neural network to detect crop leaf disease from photos", "Computer Science",
     {"eq_gpu"}, {"eq_pcr", "eq_uvvis", "eq_dso"}),
    ("Assemble and solder the PCB for a classroom weather station", "Electronics",
     {"eq_solder"}, {"eq_pcr", "eq_gpu"}),
]


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("Set GEMINI_API_KEY first.")
        return 2
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    assistant = LabAssistant.from_env(key, None, "global", model)
    names = {e.id: e.name for e in EQUIPMENT}
    failures = 0
    print(f"Model: {model}\n")
    for text, dept, must, must_not in CASES:
        started = time.monotonic()
        matches, source = assistant.suggest_needs(text, EQUIPMENT, dept)
        took = time.monotonic() - started
        got = {m.equipment_id for m in matches}
        ok = source == GEMINI and must <= got and not (got & must_not)
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {text}  ({took:.1f}s, {source})")
        for m in matches:
            print(f"        {m.tier:<8} {names[m.equipment_id]}: {m.reason}")
        if not matches:
            print("        (nothing suggested)")
        if not ok:
            print(f"        expected {sorted(must) or 'nothing'}; must not include {sorted(must_not & got)}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
