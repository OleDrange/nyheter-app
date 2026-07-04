#!/usr/bin/env python3
"""
research_briefing.py  —  Daglig forskningsbriefing med Claude AI og Notion-publisering

Henter nye fagfellevurderte studier (trening, helse og medisin) fra Europe PMC,
lar Claude velge de mest relevante og oppsummere dem i abstract-form, og publiserer
til en egen Notion-seksjon adskilt fra nyhetsbriefen.

Kjør:
    python research_briefing.py            # print til terminal
    python research_briefing.py --save     # lagrer også som markdown-fil

Miljøvariabler (deles med news_briefing.py via .env):
    ANTHROPIC_API_KEY           — påkrevd
    NOTION_API_KEY              — valgfri (for Notion-publisering)
    NOTION_PARENT_PAGE_ID       — valgfri (samme forelder-side som nyhetsbriefen)
"""

import os
import re
import sys
import html
import json
import argparse
from datetime import datetime, timedelta

# Sørg for at terminalen håndterer UTF-8 (nødvendig på Windows)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import httpx
import anthropic

# Gjenbruk hjelpefunksjoner fra nyhetsbriefen (samme mappe, ingen sideeffekter)
from news_briefing import (
    _load_dotenv,
    store_briefing,
    markdown_to_notion_blocks,
    _get_or_create_archive,
    _get_or_create_anchor,
)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — juster her
# ─────────────────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192
LOOKBACK_DAYS = 2          # vindu på publiseringsdato (toleranse for indekseringsforsinkelse)
MAX_ITEMS = 20             # maks studier i briefen (styres også i SYSTEM_PROMPT)
CANDIDATE_POOL = 30        # antall ferske studier som hentes PER KATEGORI og sendes til Claude
MAX_ABSTRACT_CHARS = 1200  # maks tegn fra hvert abstract som sendes til Claude

# Dedup mot gjentakelser på tvers av dager
SEEN_FILE = "research_seen_dois.json"
SEEN_RETENTION_DAYS = 14

# Egen Notion-seksjon (adskilt fra nyhetsbriefens "Arkiv" / "Nyhetsbriefinger")
ARCHIVE_TITLE = "Forskning Arkiv"
ANCHOR_TEXT = "Forskningsbriefinger"

# Én emnespørring per kategori — kandidater hentes separat og merkes med kategorien.
# Syntaks: Europe PMC query language. SRC:MED = kun fagfellevurdert (MEDLINE/PubMed).
# Daglig volum (LANG:eng + abstract, målt 2026-07): medisin ~4 700, kosthold ~500,
# trening ~300 nye artikler — CANDIDATE_POOL per kategori er aldri et problem å fylle.
_PMC_SUFFIX = " AND SRC:MED AND LANG:eng AND HAS_ABSTRACT:Y"
CATEGORY_QUERIES: dict[str, str] = {
    "medisin": (
        '("clinical trial" OR "randomized controlled trial" OR treatment OR therapy '
        'OR pharmacology OR cardiovascular OR metabolic OR diabetes OR obesity '
        'OR cancer OR longevity OR "public health")' + _PMC_SUFFIX
    ),
    "trening": (
        '(exercise OR "physical activity" OR "strength training" OR "resistance training" '
        'OR "aerobic exercise" OR "endurance training" OR "high-intensity interval training" '
        'OR "sports medicine" OR "muscle hypertrophy" OR recovery)' + _PMC_SUFFIX
    ),
    "kosthold": (
        '(nutrition OR diet OR "dietary intake" OR "dietary supplement" OR "weight loss" '
        'OR "intermittent fasting" OR protein OR "omega-3" OR micronutrient)' + _PMC_SUFFIX
    ),
}
CATEGORY_LABELS = {"medisin": "Medisin", "trening": "Trening", "kosthold": "Kosthold"}

_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_HEADERS = {"User-Agent": "research-briefing/1.0 (personal script)"}

SYSTEM_PROMPT = """Du lager en daglig forskningsbriefing på norsk om ny forskning innen medisin, trening og kosthold, for en interessert lekperson.

Du får en liste med kandidatstudier (kategori, tittel, tidsskrift, dato, URL, engelsk sammendrag). Velg de OPPTIL 20 mest relevante og betydningsfulle. Heller færre enn å fylle opp med svake studier. Hvis ingen er gode nok, skriv kun: "Ingen vesentlige nye studier i dag."

UTVALGSKRITERIER (prioritert rekkefølge):
- Praktisk eller klinisk betydning for medisin, trening eller kosthold
- Studiekvalitet: vekt randomiserte kontrollerte studier (RCT), metaanalyser, systematiske oversikter og store studier høyere enn små observasjonsstudier, dyrestudier og enkeltkasus
- Nyhetsverdi og bredde — velg variasjon framfor mange nesten like studier
- Fordeling: tilstreb studier fra alle tre kategoriene (minst 4 per kategori når kandidatene tillater det)

FORMAT — for hver valgte studie, nøyaktig denne strukturen:
## [Kort, dekkende norsk tittel](URL)
**Kategori:** Medisin | Trening | Kosthold (velg én — bruk kandidatens kategori, men flytt studien hvis en annen passer bedre)
**Hva som ble gjort:** Design, populasjon/antall deltakere og intervensjon. 1–3 setninger.
**Resultat:** Hovedfunn med konkrete tall (effektstørrelser, prosent, p-verdier der oppgitt). 1–3 setninger.
**Relevans:** Én setning om hva dette betyr i praksis.

REGLER:
- Bruk ALLTID den oppgitte URL-en i lenken, uendret.
- Oversett til norsk, men behold faguttrykk der det er naturlig.
- Vær konkret og tallbasert. Ingen fyllord ("det er verdt å merke seg", "i tillegg", "interessant nok").
- Ikke overdriv funn utover det sammendraget støtter. Nevn forbehold (lite utvalg, dyrestudie) kort der det er relevant.
- Ingen innledning eller oppsummering — start rett på første ## studie."""


# ─────────────────────────────────────────────────────────────────────────────
# Dedup-cache (research_seen_dois.json)
# ─────────────────────────────────────────────────────────────────────────────


def _seen_path() -> str:
    base = os.environ.get("BRIEFING_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, SEEN_FILE)


def _load_seen() -> dict:
    try:
        with open(_seen_path(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_seen(seen: dict, new_dois: list[str]) -> None:
    today = datetime.now().date().isoformat()
    for doi in new_dois:
        if doi:
            seen[doi] = today
    # Prun oppføringer eldre enn SEEN_RETENTION_DAYS (ISO-datoer kan sammenlignes som tekst)
    cutoff = (datetime.now().date() - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    seen = {doi: d for doi, d in seen.items() if d >= cutoff}
    try:
        with open(_seen_path(), "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"  ⚠  Kunne ikke skrive {SEEN_FILE}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Henting fra Europe PMC
# ─────────────────────────────────────────────────────────────────────────────


def _strip_html(text: str) -> str:
    """Fjern HTML-tagger (abstracts har f.eks. <h4>Background</h4>) og normaliser whitespace."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_research() -> list[dict]:
    """Hent kandidatstudier per kategori (medisin/trening/kosthold) og slå sammen.
    Hver artikkel merkes med `category`; duplikater på tvers av kategoriene
    (samme DOI/id) beholdes kun én gang — første kategori vinner."""
    import time as _time

    today = datetime.now().date()
    start = today - timedelta(days=LOOKBACK_DAYS)
    date_filter = f" AND (FIRST_PDATE:[{start.isoformat()} TO {today.isoformat()}])"

    seen = _load_seen()
    articles: list[dict] = []
    picked_ids: set[str] = set()
    skipped_seen = 0

    for ci, (category, cat_query) in enumerate(CATEGORY_QUERIES.items()):
        if ci:
            _time.sleep(1)  # høflig mot Europe PMC
        params = {
            "query": cat_query + date_filter,
            "resultType": "core",
            "sort": "P_PDATE_D desc",
            "pageSize": str(CANDIDATE_POOL),
            "format": "json",
        }
        try:
            resp = httpx.get(_API_URL, params=params, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"  ✗  Europe PMC ({category}): feil ved henting — {exc}")
            continue

        count = 0
        for r in data.get("resultList", {}).get("result", []):
            title = (r.get("title") or "").strip().rstrip(".")
            abstract = _strip_html(r.get("abstractText", ""))
            if not title or not abstract:
                continue

            doi = (r.get("doi") or "").strip().lower()
            if doi and doi in seen:
                skipped_seen += 1
                continue

            src = r.get("source", "")
            pid = r.get("id", "")
            uid = doi or f"{src}/{pid}"
            if uid in picked_ids:
                continue  # samme studie traff en tidligere kategorispørring
            picked_ids.add(uid)

            journal = (
                (r.get("journalInfo") or {}).get("journal", {}).get("title")
                or r.get("journalTitle")
                or "—"
            )
            if doi:
                url = f"https://doi.org/{doi}"
            elif src and pid:
                url = f"https://europepmc.org/article/{src}/{pid}"
            else:
                url = ""

            articles.append(
                {
                    "category": category,
                    "title": title,
                    "abstract": abstract[:MAX_ABSTRACT_CHARS],
                    "authors": (r.get("authorString") or "").strip(),
                    "journal": journal,
                    "date": r.get("firstPublicationDate", "—"),
                    "doi": doi,
                    "url": url,
                }
            )
            count += 1
        print(f"  ✓  {category}: {count} kandidater")

    if skipped_seen:
        print(f"  ({skipped_seen} allerede dekket tidligere — hoppet over)")
    return articles


# ─────────────────────────────────────────────────────────────────────────────
# Claude-oppsummering
# ─────────────────────────────────────────────────────────────────────────────


def build_candidates_text(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        lines.append(
            f"[{i}] ({CATEGORY_LABELS.get(a.get('category'), a.get('category', '?'))}) {a['title']}\n"
            f"Tidsskrift: {a['journal']} | Publisert: {a['date']}\n"
            f"URL: {a['url']}\n"
            f"Sammendrag: {a['abstract']}\n"
            "---"
        )
    return "\n".join(lines)


def summarize_research_with_claude(articles: list[dict]) -> str:
    client = anthropic.Anthropic()  # leser ANTHROPIC_API_KEY automatisk fra env

    today_str = datetime.now().strftime("%A %d. %B %Y")
    candidates = build_candidates_text(articles)

    user_content = (
        f"Dato: {today_str}\n\n"
        f"{len(articles)} kandidatstudier fra siste {LOOKBACK_DAYS} dager. "
        f"Velg opptil {MAX_ITEMS} av dem:\n\n"
        f"{candidates}"
    )

    print("\nVelger og oppsummerer forskning med Claude (streamer svar)...\n")
    print("─" * 70)

    collected = ""
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        for chunk in stream.text_stream:
            print(chunk, end="", flush=True)
            collected += chunk

    print()  # linjeskift etter streaming
    return collected


# ─────────────────────────────────────────────────────────────────────────────
# Notion-publisering (egen seksjon)
# ─────────────────────────────────────────────────────────────────────────────


def publish_research_to_notion(briefing: str, date_str: str, date_human: str) -> None:
    try:
        from notion_client import Client as NotionClient
    except ImportError:
        print("⚠  notion-client ikke installert. Kjør: pip install notion-client")
        return

    notion_key = os.environ.get("NOTION_API_KEY")
    parent_id = os.environ.get("NOTION_PARENT_PAGE_ID")

    if not notion_key or not parent_id:
        print("⚠  Sett NOTION_API_KEY og NOTION_PARENT_PAGE_ID for Notion-publisering.")
        return

    try:
        notion = NotionClient(auth=notion_key)
        blocks = [
            {
                "object": "block",
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [
                        {"type": "text", "text": {"content": f"Forskning — {date_human}"}}
                    ]
                },
            },
            {"object": "block", "type": "divider", "divider": {}},
        ] + markdown_to_notion_blocks(briefing)

        # Egen "Forskning Arkiv"-underside — forskningsbriefer lagres der
        archive_id = _get_or_create_archive(notion, parent_id, title=ARCHIVE_TITLE)

        CHUNK = 100
        page = notion.pages.create(
            parent={"page_id": archive_id},
            properties={
                "title": {
                    "title": [{"text": {"content": f"Forskningsbriefing {date_str}"}}]
                }
            },
            children=blocks[:CHUNK],
        )
        page_id = page["id"]

        for i in range(CHUNK, len(blocks), CHUNK):
            notion.blocks.children.append(
                block_id=page_id,
                children=blocks[i : i + CHUNK],
            )

        # Legg lenke øverst under egen anker — nyeste alltid først
        anchor_id = _get_or_create_anchor(notion, parent_id, anchor_text=ANCHOR_TEXT)
        notion.blocks.children.append(
            block_id=parent_id,
            after=anchor_id,
            children=[{
                "object": "block",
                "type": "link_to_page",
                "link_to_page": {"type": "page_id", "page_id": page_id},
            }],
        )

        page_url = page.get("url", "")
        print(f"\n✓  Publisert til Notion: Forskningsbriefing {date_str}")
        if page_url:
            print(f"   {page_url}")
    except Exception as exc:
        print(f"✗  Notion-feil: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Hovedprogram
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Daglig forskningsbriefing med Claude AI")
    parser.add_argument(
        "--save",
        action="store_true",
        help="Lagre briefingen som markdown-fil (forskningsbrief_YYYY-MM-DD.md)",
    )
    args = parser.parse_args()

    _load_dotenv()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Feil: ANTHROPIC_API_KEY er ikke satt.")
        sys.exit(1)

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_human = datetime.now().strftime("%A %d. %B %Y")

    print(f"\n{'─'*70}")
    print(f"  Forskningsbriefing  —  {today_human}")
    print(f"{'─'*70}\n")

    print(f"Henter forskning fra Europe PMC (siste {LOOKBACK_DAYS} dager)...")
    articles = fetch_research()

    if not articles:
        print("\nIngen nye studier funnet (eller alle allerede dekket). Avslutter.")
        sys.exit(0)

    print(f"  {len(articles)} kandidatstudier hentet.")

    briefing = summarize_research_with_claude(articles)

    print("─" * 70)

    # Marker valgte studier som sett (de hvis URL faktisk dukker opp i briefingen)
    selected = [a["doi"] for a in articles if a["doi"] and a["url"] and a["url"] in briefing]
    _save_seen(_load_seen(), selected)

    # Lagre forskningsbriefingen til datalageret (merges inn i samme dagsfil som nyhetsbriefen)
    research_items = [
        {"title": a["title"], "url": a["url"], "journal": a["journal"],
         "date": a["date"], "category": a["category"]}
        for a in articles
        if a["url"] and a["url"] in briefing
    ]
    store_briefing(today_str, research_md=briefing, research_items=research_items)

    # Notion
    has_notion = (
        "NOTION_API_KEY" in os.environ and "NOTION_PARENT_PAGE_ID" in os.environ
    )
    if has_notion:
        publish_research_to_notion(briefing, today_str, today_human)
    else:
        print(
            "\n💡  Tips: Sett NOTION_API_KEY og NOTION_PARENT_PAGE_ID "
            "for å publisere automatisk til Notion."
        )

    # Lagre som fil
    if args.save:
        data_dir = os.environ.get("BRIEFING_DATA_DIR", ".")
        filename = os.path.join(data_dir, f"forskningsbrief_{today_str}.md")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"# Forskningsbriefing — {today_human}\n\n" + briefing)
        print(f"✓  Lagret som {filename}")


if __name__ == "__main__":
    main()
