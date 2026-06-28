#!/usr/bin/env python3
"""
import_history.py — engangs-backfill av eksisterende markdown-briefinger.

Leser alle `briefing_YYYY-MM-DD.md` og `forskningsbrief_YYYY-MM-DD.md` i en mappe
og skriver dem inn i datalageret (<BRIEFING_DATA_DIR>/briefings/<date>.json) slik
at nettsidens arkiv ikke starter tomt.

Kjør lokalt:
    python import_history.py                 # leser fra denne mappa
    python import_history.py --src .         # eksplisitt kilde

På VPS (filene må være med i imaget / monteres inn):
    docker compose run --rm generator python import_history.py
"""

import os
import re
import glob
import argparse

from news_briefing import store_briefing

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _strip_title(md: str) -> str:
    """Fjern den ledende «# …»-tittelen; store_briefing/web legger på egen overskrift."""
    lines = md.splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill markdown-briefinger til datalageret")
    parser.add_argument("--src", default=os.path.dirname(os.path.abspath(__file__)),
                        help="Mappe å lese briefing_*.md / forskningsbrief_*.md fra")
    args = parser.parse_args()

    imported = 0

    for path in sorted(glob.glob(os.path.join(args.src, "briefing_*.md"))):
        m = DATE_RE.search(os.path.basename(path))
        if not m:
            continue
        with open(path, encoding="utf-8") as f:
            store_briefing(m.group(1), news_md=_strip_title(f.read()))
        imported += 1

    for path in sorted(glob.glob(os.path.join(args.src, "forskningsbrief_*.md"))):
        m = DATE_RE.search(os.path.basename(path))
        if not m:
            continue
        with open(path, encoding="utf-8") as f:
            store_briefing(m.group(1), research_md=_strip_title(f.read()))
        imported += 1

    print(f"\nFerdig — importerte/merget {imported} markdown-filer.")


if __name__ == "__main__":
    main()
