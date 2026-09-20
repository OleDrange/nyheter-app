#!/usr/bin/env python3
"""Vis tilsiget i quizbanken: ubrukte spørsmål per kategori og nivå, og hvor mange dager de
holder. Henter quiz_seen.json fra volumet via web-containeren (lesing, aldri `run generator`).

    python3 quiz_status.py [--target N]

`--target` er antall spørsmål hver kategori skal ha ubrukt etter runden (standard 40 —
med 7 ferske/dag fordelt på 12 kategorier trekkes ~0,6 per kategori per dag, så 40 er
drøyt to måneder). Sluttlinjen sier hvor mange som skal skrives per kategori, fordelt
jevnt på easy/medium/hard."""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
BANK = os.path.join(ROOT, "quiz_bank")
DIFFS = ("easy", "medium", "hard")
FRESH_PER_DAY = 7  # _QUIZ_FRESH_PER_DAY i news_briefing.py


def norm(t: str) -> str:
    t = re.sub(r"[^\w\s]", " ", (t or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def load_seen() -> dict:
    try:
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "web", "cat", "/data/quiz_seen.json"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        return json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        print(f"⚠  kunne ikke lese quiz_seen.json fra volumet ({exc}) — regner alt som ubrukt")
        return {}


def main() -> None:
    target = 40
    if len(sys.argv) == 3 and sys.argv[1] == "--target":
        target = int(sys.argv[2])
    seen = load_seen()
    banks = {}
    for f in sorted(os.listdir(BANK)):
        if f.endswith(".json"):
            with open(os.path.join(BANK, f), encoding="utf-8") as fh:
                banks[f[:-5]] = json.load(fh)
    per_cat_per_day = FRESH_PER_DAY / max(1, len(banks))
    print(f"{'kategori':22} {'totalt':>6} {'ubrukt':>6}  {'easy':>4} {'med':>4} {'hard':>4}  {'dager':>5}   skriv")
    todo = {}
    for slug, d in banks.items():
        qs = d.get("questions", [])
        unseen = [q for q in qs if norm(q.get("question")) not in seen]
        by = {k: sum(1 for q in unseen if q.get("difficulty") == k) for k in DIFFS}
        days = len(unseen) / per_cat_per_day
        need = max(0, target - len(unseen))
        flag = "  ← TØMT" if not unseen and qs else ("  ← ny" if not qs else "")
        print(f"{slug:22} {len(qs):6} {len(unseen):6}  {by['easy']:4} {by['medium']:4} {by['hard']:4}  {days:5.0f}   {need:3}{flag}")
        if need:
            todo[slug] = need
    total = sum(todo.values())
    print(f"\n{len(banks)} kategorier, {FRESH_PER_DAY} ferske/dag → ~{per_cat_per_day:.2f} per kategori per dag.")
    print(f"Skriv {total} spørsmål for å nå {target} ubrukte per kategori:")
    for slug, n in todo.items():
        base, rest = divmod(n, 3)
        print(f"  {slug}: {n}  (easy {base + (rest > 0)}, medium {base + (rest > 1)}, hard {base})")


if __name__ == "__main__":
    main()
