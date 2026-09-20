#!/usr/bin/env python3
"""Skriv kandidatlisten fra `--propose` som én kompakt linje per studie, gruppert per kategori.

    python list_kandidater.py kandidater.json

Løpenummeret (1–N) er det leseren stryker med, og det brukes videre i skillen — endre aldri
rekkefølgen i JSON-filen etter at listen er vist."""
import json
import sys

# Designetiketter som ikke skiller noe: alle er journal-artikler.
_NOISE = ("Journal Article", "research-article", "Research Support, Non-U.S. Gov't",
          "Research Support, N.I.H., Extramural", "review-article", "systematic-review")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1], encoding="utf-8") as f:
        items = json.load(f)
    current = None
    for i, e in enumerate(items, 1):
        if e["category"] != current:
            current = e["category"]
            print(f"\n{e['label']}")
        design = ", ".join(d for d in (e.get("design") or "").split(", ") if d and d not in _NOISE)
        print(f"{i:3d}  {e['score']:4.1f}  {design[:26]:26s}  {e['title'][:96]}")
    print(f"\n{len(items)} kandidater")


if __name__ == "__main__":
    main()
