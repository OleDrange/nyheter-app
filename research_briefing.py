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
import math
import time
import argparse
from datetime import date, datetime, timedelta

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
MAX_TOKENS = 16000  # 10 studieomtaler i ett svar (~700 tokens hver + overhead)

# Vindu på publiseringsdato. Forskning har ingen nyhetssyklus — en metaanalyse fra april er
# like relevant som en fra i går — så vi jakter ikke på det ferskeste, men på det BESTE vi
# ikke har vist før. Et bredt vindu er dessuten et *krav* for kvalitetsfiltrene under:
# Europe PMC tildeler MeSH-termer og publikasjonstyper uker etter publisering, så en artikkel
# som er to dager gammel er ennå ikke merket som menneskestudie eller RCT. Målt på `exercise`:
# 2 dager → 0 treff med MESH:"Humans"; 30 dager → 24; 180 dager → rikelig.
# 180 dager gir 494 studier i poolen (~2,7 nye i døgnet). Tilsiget er nesten identisk med
# 365 dager (3,0/døgn) — forskning publiseres jevnt — så halvårsvinduet koster nesten ingenting
# løpende; det halverer bare reservoaret vi kan tømme i starten. Til gjengjeld er alt vi viser
# publisert siste halvår.
LOOKBACK_DAYS = 180

# 5, ikke 7: vinduet tar inn ~2,6 nye studier i døgnet, så alt over det tømmer reservoaret.
# 7/dag drenerte halvårsvinduet på tre dager (23.–24. juli 2026 ga null studier).
MAX_ITEMS = 5              # maks studier i briefen (tas fra køen, ikke valgt av Claude)

# Maks studier fra samme kategori i én dagsbriefing. Køen er sortert på score alene, så uten
# taket kan fem kosthold-studier havne på samme dag og nettsiden vise én eneste gruppe.
# Taket er MYKT: har køen ikke nok kategorier, fylles dagen opp likevel — en skjev dag er
# bedre enn en tom.
MAX_PER_CATEGORY = 2

# Henting: vi paginerer gjennom HELE poolen per kategori (cursorMark), ikke bare de nyeste
# PAGE_SIZE. «Nyest først» ga oss ingenting når vinduet uansett er 180 dager — det utelukket
# bare ~80 % av materialet fra scoringen. MAX_FETCH_PER_CATEGORY er en sikkerhetsventil hvis
# en spørring plutselig eksploderer i treff (poolen er ~125 per kategori i dag).
PAGE_SIZE = 100
MAX_FETCH_PER_CATEGORY = 600

# ── Køen (research_queue.json) ──────────────────────────────────────────────
# Studier vi har vurdert lokalt, men ennå ikke vist, ligger i en VARIG kø sortert synkende på
# score. Nye studier settes inn på plassen scoren tilsier. Køen er ubegrenset i lengde.
#
# Dette erstatter den gamle karantenen (UNPICKED_COOLDOWN_DAYS = 14). Den fantes fordi vi tok
# hele beslutningen på nytt hver dag og kastet Claudes vurdering av alt vi ikke viste — eneste
# måte å slippe å betale for de samme studiene i morgen var å nekte å se på dem. Lappen spiste
# sitt eget grunnlag: 40 kandidater/dag × 14 dager = opptil ~475 studier låst ute samtidig,
# mer enn hele 180-dagersvinduet (473), og systematisk de HØYEST scorede. Målt 24. juli 2026:
# 199 av 473 satt i karantene, 160 av dem over terskel (toppscore 12,6), mens de ferske toppet
# på 2,9 — forskningsbriefingen uteble to dager på rad.
QUEUE_FILE = "research_queue.json"
QUEUE_REFILL_BELOW = 60    # under så mange «scored» i kø: hent nytt fra Europe PMC
QUEUE_MAX_AGE_DAYS = 400   # prun oppføringer eldre enn dette (på studiens publiseringsdato)

# Claude skriver omtaler i BATCH, ikke én dagsbriefing om gangen. Utvelgelsen gjøres av den
# lokale scoringen, så Claude får kun de studiene den faktisk skal skrive om — input falt fra
# ~23 000 tokens (40 kandidater å velge blant, 35 av dem kastet) til ~6 000. Teksten lagres per
# studie i køen, så hver studie koster tokens nøyaktig én gang, noensinne.
WRITEUP_REFILL_BELOW = 10  # under så mange «ready» i kø: kall Claude
WRITEUP_BATCH_SIZE = 10    # antall omtaler per Claude-kall (MAX_TOKENS må følge med)
WRITEUP_MAX_PASSES = 2     # hopper Claude over samme studie så mange ganger → rejected

# Abstractet kuttes KUN i prompten til Claude, aldri før scoringen. Resultatdelen — der
# HR/RR/CI/p og utvalgsstørrelse står — ligger typisk etter 1200 tegn, og 97 % av abstractene
# er lengre enn det. Å score på et kuttet abstract fjernet nøyaktig de signalene
# `_score_candidate` gir poeng for: målt på én dags pool falt 52 kandidater over terskel til 0.
MAX_ABSTRACT_CHARS = 4000  # maks tegn fra hvert abstract som sendes til Claude

# Robusthet mot tomt/mislykket Claude-svar (transient API-hikke gir noen ganger 0 tegn)
CLAUDE_MAX_ATTEMPTS = 3    # antall forsøk hvis streamen kommer TOM (transient hikke)
CLAUDE_RETRY_DELAY = 5     # sekunder mellom forsøk

# Robusthet mot Claudes sikkerhetsklassifikator: enkelte medisinske abstracts
# (typisk bio-relatert innhold) trigger `stop_reason == "refusal"`, som stopper HELE
# batchen. En refusal er deterministisk — retry hjelper ikke. I stedet isolerer vi
# problemabstractene med billige probe-kall og kjører oppsummeringen på nytt uten dem.
CLAUDE_REFUSAL_MAX_ROUNDS = 4   # maks antall isoler-og-fjern-runder før vi gir opp
CLAUDE_PROBE_MAX_TOKENS = 16    # små kall kun for å avgjøre refusal (ja/nei)

# Dedup mot gjentakelser på tvers av dager. TO nivåer — begge varige:
#   • vist i en briefing (picked)   → aldri vist igjen (SEEN_RETENTION_DAYS)
#   • avvist av sikkerhetsklassifikatoren (refused) → blokkert like lenge som picked. En
#     refusal er deterministisk, så å sende abstractet inn igjen ville bare utløst en ny
#     (dyr) isoler-og-fjern-runde. Lagres OGSÅ når kjøringen gir opp helt.
# Studier vi har vurdert men ikke vist, ligger i KØEN (se QUEUE_FILE) — ikke i en sperreliste.
SEEN_FILE = "research_seen_dois.json"
SEEN_RETENTION_DAYS = 400

# Egen Notion-seksjon (adskilt fra nyhetsbriefens "Arkiv" / "Nyhetsbriefinger")
ARCHIVE_TITLE = "Forskning Arkiv"
ANCHOR_TEXT = "Forskningsbriefinger"

# Én emnespørring per kategori — kandidater hentes separat og merkes med kategorien.
# Syntaks: Europe PMC query language.
#
# _PMC_SUFFIX er der utvalgskriteriene FAKTISK håndheves (før het det bare i systemprompten):
#   SRC:MED     — kun fagfellevurdert (MEDLINE/PubMed)
#   MESH:Humans — menneskestudier, ikke mus/cellekultur
#   PUB_TYPE    — kun RCT, metaanalyse eller systematisk oversikt
#
# Emneordene er bundet til TITTELEN (`TITLE:"…"`), ikke fritekst. Uten det matcher Europe PMC
# ordet hvor som helst i artikkelen, og ett tilfeldig «exercise» i et abstract om endometriose
# gjør studien til en «trenings»-studie. Målt: fritekst ga en pool full av kreft, cellegift og
# antipsykotika; tittelbinding ga treff som faktisk HANDLER om temaet. Prisen er volum
# (473 studier i et 180-dagers vindu, ~2,6 nye i døgnet) — knapt nok når vi viser opptil 5 om
# dagen; se MAX_ITEMS og MIN_SCORE for hvorfor tallet er stramt.
_PMC_SUFFIX = (
    ' AND MESH:"Humans"'
    ' AND (PUB_TYPE:"Randomized Controlled Trial" OR PUB_TYPE:"Meta-Analysis"'
    ' OR PUB_TYPE:"Systematic Review")'
    " AND SRC:MED AND LANG:eng AND HAS_ABSTRACT:Y"
)
CATEGORY_QUERIES: dict[str, str] = {
    "longevity": (
        '(TITLE:"mortality" OR TITLE:"longevity" OR TITLE:"life expectancy" OR TITLE:"aging" '
        'OR TITLE:"ageing" OR TITLE:"healthy aging" OR TITLE:"healthspan" '
        'OR TITLE:"biological age" OR TITLE:"frailty" OR TITLE:"sarcopenia" '
        'OR TITLE:"lifestyle" OR TITLE:"cardiovascular risk" OR TITLE:"older adults")'
        + _PMC_SUFFIX
    ),
    "trening": (
        '(TITLE:"exercise" OR TITLE:"physical activity" OR TITLE:"training" '
        'OR TITLE:"resistance training" OR TITLE:"strength training" OR TITLE:"aerobic" '
        'OR TITLE:"interval training" OR TITLE:"muscle strength" OR TITLE:"hypertrophy" '
        'OR TITLE:"fitness" OR TITLE:"walking" OR TITLE:"running" OR TITLE:"steps" '
        'OR TITLE:"sedentary")' + _PMC_SUFFIX
    ),
    "kosthold": (
        '(TITLE:"diet" OR TITLE:"dietary" OR TITLE:"nutrition" OR TITLE:"supplementation" '
        'OR TITLE:"supplement" OR TITLE:"protein intake" OR TITLE:"fasting" '
        'OR TITLE:"caloric restriction" OR TITLE:"weight loss" OR TITLE:"obesity" '
        'OR TITLE:"vitamin" OR TITLE:"omega-3" OR TITLE:"creatine" OR TITLE:"caffeine" '
        # «fiber» alene matcher «Thulium Fiber Laser» — kostfiber må sies eksplisitt.
        'OR TITLE:"alcohol" OR TITLE:"dietary fiber" OR TITLE:"probiotic")' + _PMC_SUFFIX
    ),
    # NB: «stress» og «recovery» kan IKKE stå alene — de matcher «oxidative stress» og
    # postoperativ restitusjon, og dro inn prostata-MR og hjertekirurgi i poolen. Kun fraser.
    "sovn_stress": (
        '(TITLE:"sleep" OR TITLE:"insomnia" OR TITLE:"circadian" OR TITLE:"mindfulness" '
        'OR TITLE:"meditation" OR TITLE:"psychological stress" OR TITLE:"perceived stress" '
        'OR TITLE:"stress reduction" OR TITLE:"stress management" OR TITLE:"chronic stress" '
        'OR TITLE:"burnout" OR TITLE:"resilience" OR TITLE:"anxiety" OR TITLE:"depression" '
        'OR TITLE:"wellbeing" OR TITLE:"well-being" OR TITLE:"mental health" '
        'OR TITLE:"cognitive behavioral therapy")' + _PMC_SUFFIX
    ),
}
CATEGORY_LABELS = {
    "longevity": "Longevity",
    "trening": "Trening",
    "kosthold": "Kosthold",
    "sovn_stress": "Søvn og stress",
    "medisin": "Medisin",  # legacy — kun i arkiverte briefinger
}

_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_HEADERS = {"User-Agent": "research-briefing/1.0 (personal script)"}

SYSTEM_PROMPT = """Du lager en daglig forskningsbriefing på norsk for én bestemt leser: en oppegående lekperson som er opptatt av LONGEVITY — å leve lenge og friskt — og som vil vite hva han selv kan gjøre. Han bryr seg om trening, kosthold, søvn og stress, og han vil ha studier med tydelige tall han kan stole på.

Du får en liste med studier (kategori, tittel, tidsskrift, dato, URL, engelsk sammendrag). Alle er allerede menneskestudier av typen RCT, metaanalyse eller systematisk oversikt, og alle er forhåndsrangert som relevante.

Skriv en omtale av HVER studie i listen, i den rekkefølgen de står. Du skal ikke velge mellom dem — utvalget er gjort.

VRAKING: er en studie likevel ubrukelig for denne leseren, skal du IKKE skrive en omtale av den. Skriv i stedet nøyaktig denne linjen, på egen linje:
## SKIP [n] — kort begrunnelse
der [n] er studiens nummer i listen. Vrak kun når ett av disse er oppfylt:
1. Ingen konkrete tall — sammendraget sier bare "signifikant bedring" uten effektstørrelser (prosent, HR/RR/OR med konfidensintervall, SMD, absolutte endringer).
2. Ingen handlingsrom — dette er klinisk behandling leseren aldri selv vil ta stilling til.
3. Studien er så svak eller så smal at et råd bygget på den ville villede.
Vraking skal være unntaket. Er du i tvil, skriv omtalen.

FORMAT — for hver studie du skriver om, nøyaktig denne strukturen:
## [Norsk tittel som bærer hovedfunnet](URL)
**Kategori:** Longevity | Trening | Kosthold | Søvn og stress (velg én — bruk kandidatens kategori, men flytt studien hvis en annen passer bedre)
**Metode:** Hva slags studie er dette (RCT, metaanalyse av N studier, systematisk oversikt), hvor mange deltakere, hvem var de (alder, kjønn, helsetilstand), hvor lenge varte det, og hva gikk intervensjonen eller eksponeringen konkret ut på? Forklar designet slik at leseren skjønner hvorfor det gir grunn til å tro på resultatet. 3–4 setninger.
**Resultat:** Hovedfunnene med konkrete tall — effektstørrelse, prosentvis endring, HR/RR/OR med konfidensintervall, p-verdi der den er oppgitt. Si alltid hva det ble sammenlignet MOT (kontrollgruppe, placebo, ingen endring). Ta med de viktigste sekundærfunnene hvis de er interessante. 3–4 setninger.
**Hva det betyr for deg:** Oversett funnet til handling. Hvilken dose, frekvens eller mengde er det snakk om i praksis? Er effekten stor nok til å bry seg om? Hva bør leseren eventuelt endre — eller hva bekrefter dette at han kan fortsette med? Vær konkret; ingen runde formuleringer. 3–4 setninger.
**Forbehold:** Hva studien IKKE viser. Observasjonsdata kan ikke vise årsak; kort varighet sier ingenting om livslang effekt; et smalt utvalg (kun eliteutøvere, kun eldre kvinner) generaliserer dårlig; industrifinansiering, høy heterogenitet eller lav studiekvalitet i en metaanalyse svekker konklusjonen. 1–2 setninger.

REGLER:
- Tittelen skal si HVA studien fant — resultat, retning og tall der det finnes — ikke bare temaet. Godt: «Styrketrening to ganger i uka ga 15 % lavere dødelighet». Dårlig: «Metaanalyse om styrketrening og dødelighet». Tittelen leses alene i en lenkeliste og må stå på egne ben.
- Bruk ALLTID den oppgitte URL-en i lenken, uendret.
- Oversett til norsk, men behold faguttrykk der det er naturlig (RCT, metaanalyse, konfidensintervall).
- Forklar forkortelser og mål første gang de brukes (f.eks. "HR 0,78" → "78 % av risikoen i kontrollgruppen").
- Vær konkret og tallbasert. Ingen fyllord ("det er verdt å merke seg", "i tillegg", "interessant nok").
- Ikke overdriv funn utover det sammendraget støtter. Ikke dikt opp tall som ikke står i sammendraget.
- Ingen innledning eller oppsummering — start rett på første ## studie."""

# Skillet mellom studier i den lagrede markdownen. Claude produserer det selv i dag; når vi
# setter sammen en briefing av lagrede omtaler, føyer vi det inn mellom blokkene.
_STUDY_SEPARATOR = "\n\n---\n\n"


# ─────────────────────────────────────────────────────────────────────────────
# Dedup-cache (research_seen_dois.json)
# ─────────────────────────────────────────────────────────────────────────────


def _seen_path() -> str:
    base = os.environ.get("BRIEFING_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, SEEN_FILE)


def _load_seen() -> dict:
    """Les dedup-cachen og normaliser til
    {doi: {"last": dato, "picked": bool, "refused": bool}}.

    Bakoverkompatibel: det gamle formatet lagret en ren datostreng per DOI, og alle
    oppføringene der var studier Claude faktisk valgte — de tolkes som picked=True.
    Oppføringer skrevet før refused-flagget fantes leses som refused=False."""
    try:
        with open(_seen_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    seen: dict[str, dict] = {}
    for doi, val in (raw or {}).items():
        if isinstance(val, str):  # gammelt format: bare datoen
            seen[doi] = {"last": val, "picked": True, "refused": False}
        elif isinstance(val, dict) and val.get("last"):
            seen[doi] = {
                "last": val["last"],
                "picked": bool(val.get("picked")),
                "refused": bool(val.get("refused")),
            }
    return seen


def _is_blocked(seen: dict, doi: str, today: date | None = None) -> bool:
    """Skal denne DOI-en holdes utenfor køen?

    Kun to grunner, begge varige: studien er allerede VIST leseren (picked — han skal aldri
    se den to ganger), eller den ble avvist av sikkerhetsklassifikatoren (refused — en
    refusal er deterministisk, så nytt forsøk koster bare en ny bisect-runde).

    Merk at oppføringer som verken er picked eller refused ikke blokkerer i det hele tatt.
    Det er de gamle karanteneoppføringene fra UNPICKED_COOLDOWN_DAYS-tiden; de slutter å
    blokkere av seg selv, uten migreringsskript."""
    entry = seen.get(doi)
    if not entry:
        return False
    if not (entry["picked"] or entry.get("refused")):
        return False
    cutoff = ((today or datetime.now().date()) - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    return entry["last"] >= cutoff


def _save_seen(
    seen: dict,
    picked_dois: list[str],
    refused_dois: list[str] | None = None,
) -> None:
    """Merk studier som varig blokkert. `picked_dois` er de som ble VIST i en briefing,
    `refused_dois` de som trigget sikkerhetsklassifikatoren. Begge flaggene er klebrige —
    en DOI som først ble avvist og senere vises (eller omvendt), beholder begge."""
    today = datetime.now().date().isoformat()
    picked = set(picked_dois)
    refused = set(refused_dois or [])
    for doi in picked | refused:
        if not doi:
            continue
        prev = seen.get(doi, {})
        seen[doi] = {
            "last": today,
            "picked": doi in picked or prev.get("picked", False),
            "refused": doi in refused or prev.get("refused", False),
        }

    # Prun oppføringer som ikke lenger kan blokkere noe (ISO-datoer sammenlignes som tekst).
    keep_from = (datetime.now().date() - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    seen = {doi: e for doi, e in seen.items() if e["last"] >= keep_from}
    try:
        with open(_seen_path(), "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"  ⚠  Kunne ikke skrive {SEEN_FILE}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Køen (research_queue.json)
#
# Én varig liste, sortert synkende på score, ubegrenset i lengde. Hver oppføring har en
# status:
#   • "scored"   — vurdert lokalt, venter på at Claude skal skrive omtalen
#   • "ready"    — har ferdig `writeup`, klar til publisering (abstract er slettet)
#   • "rejected" — Claude vraket den, eller den trigget sikkerhetsklassifikatoren.
#                  Blir liggende som gravstein så den ikke settes inn igjen ved neste henting.
#
# Køen er sin egen dedup: en DOI som allerede står der, settes aldri inn på nytt.
# MÅ persisteres — ligger i BRIEFING_DATA_DIR (volumet), sammen med research_seen_dois.json.
# ─────────────────────────────────────────────────────────────────────────────


def _queue_path() -> str:
    base = os.environ.get("BRIEFING_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, QUEUE_FILE)


def _load_queue() -> list[dict]:
    """Les køen. Manglende eller korrupt fil → tom kø (systemet bygger seg opp igjen ved
    neste henting; det eneste som går tapt er Claude-tekster vi allerede har betalt for)."""
    try:
        with open(_queue_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    entries = (raw or {}).get("entries") or []
    return [e for e in entries if isinstance(e, dict) and e.get("doi")]


def _save_queue(queue: list[dict]) -> None:
    """Skriv køen sortert synkende på score. Atomisk (.tmp + os.replace), som
    store_briefing() — en halvskrevet kø ville kostet både tekster og rekkefølge."""
    queue.sort(key=lambda e: e.get("score", 0.0), reverse=True)
    payload = {
        "version": 1,
        "updated": datetime.now().date().isoformat(),
        "entries": queue,
    }
    path = _queue_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        print(f"  ⚠  Kunne ikke skrive {QUEUE_FILE}: {exc}")


def _queue_counts(queue: list[dict]) -> dict[str, int]:
    counts = {"scored": 0, "ready": 0, "rejected": 0}
    for e in queue:
        counts[e.get("status", "scored")] = counts.get(e.get("status", "scored"), 0) + 1
    return counts


def _prune_queue(queue: list[dict], seen: dict, today: date) -> list[dict]:
    """Fjern oppføringer som ikke lenger hører hjemme i køen.

    To grunner: studien er for gammel (QUEUE_MAX_AGE_DAYS på publiseringsdato — en studie skal
    ikke ligge og eldes i køen i det uendelige), eller den er allerede vist leseren. Det siste
    er et sikkerhetsnett: køen og seen skal ikke kunne komme i utakt og gi dobbeltvisning."""
    cutoff = (today - timedelta(days=QUEUE_MAX_AGE_DAYS)).isoformat()
    kept, dropped_old, dropped_seen = [], 0, 0
    for e in queue:
        pub = (e.get("date") or "")[:10]
        if pub and pub < cutoff:
            dropped_old += 1
            continue
        if e.get("doi") and _is_blocked(seen, e["doi"], today):
            dropped_seen += 1
            continue
        kept.append(e)
    if dropped_old or dropped_seen:
        print(f"  ⓘ  prunet kø: {dropped_old} for gamle, {dropped_seen} allerede vist")
    return kept


def _insert_scored(queue: list[dict], article: dict, score: float, why: str) -> bool:
    """Sett en nyscoret studie inn i køen. Returnerer False hvis DOI-en allerede står der.

    Sorteringen gjøres i _save_queue(), så plasseringen «etter hvor god studien er» faller
    ut av seg selv — listen er noen hundre lang, så en full sortering er gratis."""
    doi = article.get("doi") or ""
    key = doi or article.get("url") or article.get("title")
    for e in queue:
        if (e.get("doi") or e.get("url") or e.get("title")) == key:
            return False
    queue.append({
        "doi": doi,
        "url": article["url"],
        "title": article["title"],
        "journal": article["journal"],
        "date": article["date"],
        "category": article["category"],
        "pub_types": article.get("pub_types") or [],
        "abstract": article["abstract"],
        "score": round(score, 2),
        "score_why": why,
        "queued_at": datetime.now().date().isoformat(),
        "status": "scored",
        "passes": 0,
        "writeup": None,
        "writeup_at": None,
    })
    return True


def _entry_to_article(entry: dict) -> dict:
    """Køoppføring → artikkel-dict slik resten av koden (prompt, refusal-bisect) forventer."""
    return {
        "category": entry.get("category"),
        "title": entry.get("title", ""),
        "abstract": entry.get("abstract") or "",
        "journal": entry.get("journal", "—"),
        "date": entry.get("date", "—"),
        "doi": entry.get("doi", ""),
        "url": entry.get("url", ""),
        "pub_types": entry.get("pub_types") or [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Henting fra Europe PMC
# ─────────────────────────────────────────────────────────────────────────────


def _strip_html(text: str) -> str:
    """Fjern HTML-tagger (abstracts har f.eks. <h4>Background</h4>) og normaliser whitespace."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Lokal scoring — grovsorteringen skjer HER, ikke hos Claude
#
# Europe PMC-spørringen garanterer allerede menneskestudie + RCT/metaanalyse/oversikt.
# Scoringen rangerer det som er igjen etter det leseren faktisk er ute etter: tydelige tall,
# harde utfall, og noe han kan gjøre selv. Scoringen bestemmer BÅDE hva som kommer i kø og
# rekkefølgen i den — Claude velger ikke lenger, den skriver. Det er derfor input falt fra
# ~23 000 tokens (40 kandidater å velge blant, 35 av dem kastet) til ~6 000 per kall.
# ─────────────────────────────────────────────────────────────────────────────

# Terskelen var midlertidig senket til 1,5 mens karantenen låste ute de gode studiene. Med
# køen fanges de opp i stedet for å blokkeres, så kvalitetskravet kan stå: studier under
# terskelen settes aldri i kø — heller færre enn svake.
MIN_SCORE = 3.0

# Studiedesign (matches mot pubTypeList)
_DESIGN_POINTS = [
    ("meta-analysis", 4.0),
    ("randomized controlled trial", 3.0),
    ("systematic review", 2.0),
]

# Tydelige statistiske resultater — selve kravet: «studier med tydelige tall».
_STATS_PATTERNS = [
    r"\b95\s*%?\s*(ci|konfidens)",           # 95% CI
    r"\bci\b\s*[:=]?\s*[\[(]",               # CI [0.71, 0.94]
    r"\b(hazard ratio|hr)\s*[:=]?\s*\d",     # HR = 0.78
    r"\b(risk ratio|relative risk|rr)\s*[:=]?\s*\d",
    r"\b(odds ratio|or)\s*[:=]?\s*\d",
    r"\b(smd|standardi[sz]ed mean difference)\b",
    r"\b(md|mean difference)\s*[:=]?\s*-?\d",
    r"\bp\s*[<=>]\s*0?\.\d",                 # p < 0.05
    r"\b\d{1,3}(\.\d+)?\s*%\s*(lower|higher|reduction|increase|decrease|greater)",
]

# Harde/relevante utfall — det som faktisk betyr noe for et langt, friskt liv.
_OUTCOME_TERMS = [
    "all-cause mortality", "mortality", "life expectancy", "longevity", "healthspan",
    "cardiovascular", "cardiorespiratory fitness", "vo2", "blood pressure", "hba1c",
    "insulin sensitivity", "ldl", "body composition", "lean mass", "muscle mass",
    "muscle strength", "sarcopenia", "frailty", "bone density", "cognition",
    "cognitive decline", "dementia", "depression", "sleep quality", "sleep duration",
    "biological age", "epigenetic age", "inflammation", "visceral fat", "type 2 diabetes",
]

# Smale pasientgrupper og ren klinikk. Tittelbindingen i spørringen sikrer at studien HANDLER
# om trening/kosthold/søvn — men en RCT på trening hos pasienter med aksial spondylartritt eller
# hos slagpasienter under rehabilitering sier lite om hva en frisk leser bør gjøre. Vektes ned
# hardt, og på TITTELEN, som er der studiepopulasjonen faktisk står.
_NARROW_POPULATION = [
    # «patients with …» er det mest treffsikre enkeltsignalet på at studien gjelder en
    # pasientgruppe leseren ikke tilhører.
    "patients with", "patients undergoing", "in patients",
    "cancer", "tumor", "tumour", "chemotherapy", "radiotherapy", "oncolog", "leukemia",
    "myeloma", "lymphoma", "prostate", "palliative", "survivors",
    "stroke", "parkinson", "alzheimer", "dementia patients", "schizophrenia", "psychiatric",
    "bipolar", "psychosis", "autism", "adhd", "epilepsy", "multiple sclerosis",
    "cerebral palsy", "spinal cord injury", "traumatic brain injury",
    "dialysis", "hemodialysis", "kidney disease", "cirrhosis", "hepatitis", "hiv",
    "copd", "cystic fibrosis", "asthma", "spondyl", "arthritis", "fibromyalgia", "lupus",
    "preoperative", "postoperative", "perioperative", "procedural", "surgery", "surgical",
    "anesthesia", "anaesthesia", "rehabilitation", "intensive care", "mechanical ventilation",
    "sepsis", "transplant", "prosthesis", "denture", "dental", "orthodontic",
    "wound healing", "catheter", "amputation", "long covid",
    "neonatal", "preterm", "perinatal", "pediatric", "paediatric", "children", "adolescent",
    "pregnan", "postpartum", "menopaus", "infertility", "dysmenorrhea", "endometriosis",
]

# Medikament-/prosedyre-/apparatintervensjoner: leseren tar aldri stilling til dette selv.
_DRUG_TERMS = [
    "drug", "pharmacolog", "antipsychotic", "antidepressant", "anticoagulant", "statin",
    "metformin", "semaglutide", "colchicine", "corticosteroid", "chemotherap",
    "immunotherap", "vaccine", "antibiotic", "acupuncture", "monoclonal",
    "inhibitor", "agonist", "antagonist",
    "transcranial", "electrical stimulation", "magnetic stimulation", "photobiomodulation",
    "laser", "lithotripsy",
    # Genetikk: interessant, men ikke noe leseren kan handle på.
    "polymorphism", "genotype", "gene variant", "mendelian randomization",
]

_N_PATTERNS = [
    r"\bn\s*=\s*([\d,\. ]{2,12})",
    r"([\d,\. ]{2,12})\s*(participants|patients|adults|subjects|individuals|men|women)",
    r"(?:including|involving|comprising)\s+([\d,\. ]{2,12})\s",
]


def _extract_sample_size(text: str) -> int:
    """Største plausible deltakerantall i abstractet (0 hvis ingen funnet).
    Vi tar det STØRSTE treffet fordi abstracts ofte nevner både delgrupper og totalen."""
    best = 0
    for pat in _N_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            raw = re.sub(r"[,\. ]", "", m.group(1) or "")
            if raw.isdigit():
                n = int(raw)
                if 10 <= n <= 50_000_000:  # filtrer bort årstall/doser/ID-er
                    best = max(best, n)
    return best


def _score_candidate(article: dict) -> tuple[float, str]:
    """Rangér en kandidat. Returnerer (score, kort begrunnelse for terminalloggen).

    Emnet er allerede garantert av tittelbindingen i spørringen, så scoringen rangerer på
    det som skiller en studie leseren kan BRUKE fra en han ikke kan: tydelige tall, harde
    utfall, robust utvalg — og at funnet gjelder folk som ham, ikke en smal pasientgruppe."""
    title = article["title"].lower()
    text = f"{title} {article['abstract']}".lower()
    pub_types = " ".join(article.get("pub_types") or []).lower()
    score = 0.0
    why = []

    for name, pts in _DESIGN_POINTS:
        if name in pub_types:
            score += pts
            why.append(name.split()[0])
            break  # kun den sterkeste designtypen teller

    n = _extract_sample_size(text)
    if n:
        # log10 dempet: 100 deltakere → 1,6; 10 000 → 3,2. Store studier vinner, men en
        # gigantisk kohort om noe irrelevant skal ikke kunne kjøpe seg til toppen.
        score += min(0.8 * math.log10(n), 4.0)
        why.append(f"n≈{n:,}".replace(",", " "))

    stats_hits = sum(1 for pat in _STATS_PATTERNS if re.search(pat, text))
    if stats_hits:
        score += min(1.0 * stats_hits, 3.0)
        why.append(f"{stats_hits} tallsignal")
    else:
        score -= 3.0  # ingen konkrete effektmål → ingenting å skrive «Resultat» av

    outcome_hits = sum(1 for t in _OUTCOME_TERMS if t in text)
    if outcome_hits:
        score += min(0.8 * outcome_hits, 3.0)
        why.append(f"{outcome_hits} utfall")

    # Smal pasientgruppe / ren klinikk: straffes på TITTELEN (der populasjonen står), og
    # svakere på abstractet (en nevnt bisetning skal ikke drepe en ellers god studie).
    narrow_title = sum(1 for t in _NARROW_POPULATION if t in title)
    if narrow_title:
        score -= 4.0 * narrow_title
        why.append(f"−smal populasjon×{narrow_title}")
    elif any(t in article["abstract"].lower() for t in _NARROW_POPULATION):
        score -= 1.0
        why.append("−klinisk kontekst")

    drug_hits = sum(1 for t in _DRUG_TERMS if t in title)
    if drug_hits:
        score -= 4.0 * drug_hits
        why.append(f"−medikament×{drug_hits}")

    return score, ", ".join(why)


def _fetch_all_pages(query: str) -> list[dict]:
    """Paginer gjennom hele treffmengden for én spørring via Europe PMCs cursorMark.

    Stopper når API-et gjentar cursoren (siste side) eller MAX_FETCH_PER_CATEGORY er nådd.
    En feil på side 2+ kaster ikke bort sidene vi allerede har — vi returnerer det vi fikk."""
    out: list[dict] = []
    cursor = "*"
    while len(out) < MAX_FETCH_PER_CATEGORY:
        params = {
            "query": query,
            "resultType": "core",
            "sort": "P_PDATE_D desc",
            "pageSize": str(PAGE_SIZE),
            "format": "json",
            "cursorMark": cursor,
        }
        try:
            resp = httpx.get(_API_URL, params=params, headers=_HEADERS, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            if out:
                break  # myk feil: behold sidene vi rakk å hente
            raise
        results = data.get("resultList", {}).get("result", [])
        out.extend(results)
        next_cursor = data.get("nextCursorMark")
        if not results or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return out


def refill_queue(queue: list[dict], seen: dict, today: date) -> int:
    """Hent nye studier fra Europe PMC og sett dem inn i køen. Returnerer antall innsatte.

    Per kategori: paginer gjennom HELE poolen som matcher de harde filtrene, fjern det
    leseren allerede har sett, og score lokalt på fullt abstract. Alt som når MIN_SCORE
    settes inn i køen på plassen scoren tilsier — det er ingen dagskvote lenger, for køen
    er ikke et dagsutvalg. Kategoribalansen ivaretas ved UTTAK (_pop_for_today), som er
    riktig sted: der vet vi hva som faktisk skal vises.

    Duplikater beholdes kun én gang — både på tvers av kategorispørringene og mot det som
    allerede står i køen."""
    import time as _time

    start = today - timedelta(days=LOOKBACK_DAYS)
    date_filter = f" AND (FIRST_PDATE:[{start.isoformat()} TO {today.isoformat()}])"

    seen_ids: set[str] = set()
    skipped_seen = 0
    skipped_weak = 0
    skipped_dupe = 0
    inserted = 0

    for ci, (category, cat_query) in enumerate(CATEGORY_QUERIES.items()):
        if ci:
            _time.sleep(1)  # høflig mot Europe PMC
        try:
            results = _fetch_all_pages(cat_query + date_filter)
        except Exception as exc:
            print(f"  ✗  Europe PMC ({category}): feil ved henting — {exc}")
            continue

        cat_inserted = 0
        for r in results:
            title = (r.get("title") or "").strip().rstrip(".")
            abstract = _strip_html(r.get("abstractText", ""))
            if not title or not abstract:
                continue

            doi = (r.get("doi") or "").strip().lower()
            if doi and _is_blocked(seen, doi, today):
                skipped_seen += 1
                continue

            src = r.get("source", "")
            pid = r.get("id", "")
            uid = doi or f"{src}/{pid}"
            if uid in seen_ids:
                continue  # samme studie traff en tidligere kategorispørring
            seen_ids.add(uid)

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

            article = {
                "category": category,
                "title": title,
                # Fullt abstract — kuttes først i prompten (build_candidates_text).
                "abstract": abstract,
                "authors": (r.get("authorString") or "").strip(),
                "journal": journal,
                "date": r.get("firstPublicationDate", "—"),
                "doi": doi,
                "url": url,
                "pub_types": (r.get("pubTypeList") or {}).get("pubType") or [],
            }
            score, why = _score_candidate(article)
            if score < MIN_SCORE:
                skipped_weak += 1
                continue
            if _insert_scored(queue, article, score, why):
                cat_inserted += 1
            else:
                skipped_dupe += 1

        inserted += cat_inserted
        print(f"  ✓  {category}: {cat_inserted} nye i kø (av {len(results)} hentet)")

    if skipped_seen:
        print(f"  ({skipped_seen} allerede vist tidligere — hoppet over)")
    if skipped_dupe:
        print(f"  ({skipped_dupe} sto allerede i køen)")
    if skipped_weak:
        print(f"  ({skipped_weak} forkastet av lokal scoring — under terskel {MIN_SCORE})")
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Claude-oppsummering
# ─────────────────────────────────────────────────────────────────────────────


def build_candidates_text(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        design = ", ".join(a.get("pub_types") or []) or "—"
        lines.append(
            f"[{i}] ({CATEGORY_LABELS.get(a.get('category'), a.get('category', '?'))}) {a['title']}\n"
            f"Design: {design} | Tidsskrift: {a['journal']} | Publisert: {a['date']}\n"
            f"URL: {a['url']}\n"
            f"Sammendrag: {a['abstract'][:MAX_ABSTRACT_CHARS]}\n"
            "---"
        )
    return "\n".join(lines)


def _build_user_content(articles: list[dict]) -> str:
    today_str = datetime.now().strftime("%A %d. %B %Y")
    return (
        f"Dato: {today_str}\n\n"
        f"{len(articles)} studier, forhåndsfiltrert til menneskestudier "
        f"(RCT / metaanalyse / systematisk oversikt) fra de siste {LOOKBACK_DAYS} dagene og "
        f"rangert lokalt på relevans. Skriv en omtale av hver av dem:\n\n"
        f"{build_candidates_text(articles)}"
    )


def _stream_summary(client: "anthropic.Anthropic", articles: list[dict]) -> tuple[str, str | None]:
    """Ett streaming-kall. Returnerer (tekst, stop_reason).

    En sikkerhets-refusal gir TOM tekst uten å kaste unntak — streamen leverer
    da null tekst-chunks, og `stop_reason == "refusal"`. Vi henter derfor
    stop_reason fra sluttmeldingen slik at kalleren kan skille refusal fra en
    transient tom hikke."""
    collected = ""
    stop_reason: str | None = None
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_content(articles)}],
        ) as stream:
            for chunk in stream.text_stream:
                print(chunk, end="", flush=True)
                collected += chunk
            stop_reason = stream.get_final_message().stop_reason
    except Exception as exc:  # nettverks-/API-feil under streaming
        print(f"\n⚠  Claude-kall feilet: {exc}")
    print()  # linjeskift etter streaming
    return collected, stop_reason


def _batch_refuses(client: "anthropic.Anthropic", articles: list[dict]) -> bool:
    """Billig probe (max_tokens=16, ikke streaming): trigger dette kandidatsettet
    sikkerhets-refusal? Bruker SAMME input-form som det ekte kallet, så svaret
    stemmer med hva `_stream_summary` ville gjort. En transient API-feil under
    proben tolkes konservativt som «ikke refusal» (vi vil ikke kaste bort gode
    abstracts på en nettverkshikke)."""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=CLAUDE_PROBE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_content(articles)}],
        )
    except Exception as exc:
        print(f"  ⚠  Refusal-probe feilet ({exc}) — antar ikke-refusal.")
        return False
    return resp.stop_reason == "refusal"


def _find_refusing_articles(
    client: "anthropic.Anthropic", articles: list[dict]
) -> list[dict]:
    """Bisect fram til abstractene som trigger refusal, med billige probe-kall.
    Returnerer settet som bør fjernes. En enkelt «giftig» abstract avvises i
    enhver delmengde den er med i, så halvering isolerer den i O(k·log n) kall.
    Dersom en halvdel avvises som KOMBINASJON (ingen enkeltdel avvises alene),
    fjernes den minste halvdelen for å bryte kombinasjonen."""

    def bisect(subset: list[dict]) -> list[dict]:
        if not _batch_refuses(client, subset):
            return []
        if len(subset) == 1:
            return list(subset)
        mid = len(subset) // 2
        left, right = subset[:mid], subset[mid:]
        bad = bisect(left) + bisect(right)
        if bad:
            return bad
        # Refusal skyldes en kombinasjon på tvers av halvdelene, ikke én artikkel.
        return list(left if len(left) <= len(right) else right)

    return bisect(articles)


_SKIP_RE = re.compile(r"^##\s*SKIP\b\s*\[?(\d+)\]?", re.IGNORECASE)


def _parse_writeups(text: str, articles: list[dict]) -> tuple[dict[str, str], set[str]]:
    """Del Claudes svar i én blokk per studie. Returnerer ({url: omtale}, {url som ble vraket}).

    Blokkene mappes på URL, ikke på rekkefølge: prompten krever at URL-en gjengis uendret, og
    en feilmapping ville gitt leseren feil lenke under riktig tittel. Blokker som ikke kan
    knyttes til en studie, forkastes stille — vi lagrer aldri tekst vi ikke vet hva er.

    Formatet er identisk med det Claude produserer i dag (`## [tittel](url)` + de merkede
    avsnittene), fordi nettsidens splitResearch() parser nettopp den overskriften. Derfor kan
    en omtale skrevet for tre dager siden settes sammen med en skrevet i dag."""
    blocks: dict[str, str] = {}
    skipped: set[str] = set()
    by_url = {a["url"]: a for a in articles if a.get("url")}

    for raw in re.split(r"\n(?=##\s)", text.strip()):
        block = raw.strip().strip("-").strip()
        if not block.startswith("##"):
            continue

        m = _SKIP_RE.match(block)
        if m:
            idx = int(m.group(1)) - 1  # prompten nummererer fra 1
            if 0 <= idx < len(articles):
                skipped.add(articles[idx]["url"])
            continue

        for url, _a in by_url.items():
            if url and url in block:
                blocks[url] = block
                break
    return blocks, skipped


def write_up_with_claude(articles: list[dict]) -> tuple[str, list[str]]:
    """Be Claude skrive en omtale av HVER studie i `articles` (ingen utvelgelse — den er
    allerede gjort av den lokale scoringen).

    Returnerer (rå markdown, refused_dois). `refused_dois` er DOI-ene til abstracts som ble
    fjernet fordi de trigget sikkerhetsklassifikatoren — kalleren skal persistere dem
    (refused-flagget) uansett om kallet lyktes, så de aldri sendes inn igjen."""
    client = anthropic.Anthropic()  # leser ANTHROPIC_API_KEY automatisk fra env

    pool = list(articles)
    refused_dois: list[str] = []

    print(f"\nSkriver omtaler av {len(pool)} studier med Claude (streamer svar)...\n")
    print("─" * 70)

    # To feilmoduser håndteres ulikt:
    #  • TOM streng uten refusal  → transient hikke; prøv på nytt (samme input).
    #  • stop_reason == "refusal" → sikkerhetsklassifikatoren stoppet batchen;
    #    deterministisk, så retry er nytteløst. Isolér og fjern problemabstract(er),
    #    kjør så på nytt med det rensede settet.
    # Claude vraker enkeltstudier med «## SKIP», aldri ved å svare tomt, så blank tekst er
    # alltid en feil.
    transient_attempts = 0
    refusal_rounds = 0
    while True:
        collected, stop_reason = _stream_summary(client, pool)
        if collected.strip():
            return collected, refused_dois

        if stop_reason == "refusal":
            refusal_rounds += 1
            if refusal_rounds > CLAUDE_REFUSAL_MAX_ROUNDS:
                print("✗  For mange avvisningsrunder — gir opp forskningsbriefingen.")
                return "", refused_dois
            print("\n⚠  Claude avviste batchen (sikkerhetsklassifikator). "
                  "Isolerer problemabstract(er)...")
            bad = _find_refusing_articles(client, pool)
            if not bad:
                print("✗  Fant ingen enkeltabstract å fjerne — gir opp.")
                return "", refused_dois
            drop_ids = {id(a) for a in bad}
            for a in bad:
                print(f"    – fjernet: {a['title'][:90]}")
                if a.get("doi"):
                    refused_dois.append(a["doi"])
            pool = [a for a in pool if id(a) not in drop_ids]
            print(f"  {len(pool)} kandidater igjen — prøver på nytt.")
            if not pool:
                print("✗  Ingen kandidater igjen etter filtrering — gir opp.")
                return "", refused_dois
            print("─" * 70)
            continue

        # Tom uten refusal → transient.
        transient_attempts += 1
        if transient_attempts >= CLAUDE_MAX_ATTEMPTS:
            return "", refused_dois  # tom etter alle forsøk — main() håndterer dette
        print(f"⚠  Tomt svar fra Claude — nytt forsøk om {CLAUDE_RETRY_DELAY} s "
              f"({transient_attempts}/{CLAUDE_MAX_ATTEMPTS})...")
        time.sleep(CLAUDE_RETRY_DELAY)


def write_up_batch(queue: list[dict]) -> list[str]:
    """Få Claude til å skrive omtaler av de høyest scorede studiene som mangler tekst, og
    lagre dem I KØEN. Returnerer refused_dois (kalleren persisterer dem).

    Dette er eneste sted i systemet som bruker Claude-tokens. Alt som skrives her, lagres —
    en studie koster tokens nøyaktig én gang, noensinne."""
    batch_entries = [e for e in queue if e.get("status") == "scored"][:WRITEUP_BATCH_SIZE]
    if not batch_entries:
        print("  ⚠  Ingen studier i kø å skrive om.")
        return []

    articles = [_entry_to_article(e) for e in batch_entries]
    text, refused_dois = write_up_with_claude(articles)
    print("─" * 70)

    refused = set(refused_dois)
    for e in batch_entries:
        if e.get("doi") and e["doi"] in refused:
            e["status"] = "rejected"
            e["reject_reason"] = "refusal"
            e.pop("abstract", None)

    if not text.strip():
        print("✗  Tomt svar fra Claude — ingen omtaler lagret.")
        return refused_dois

    blocks, skipped = _parse_writeups(text, articles)
    if not blocks and not skipped:
        # Svaret kunne ikke mappes til noen studie. Å lagre noe her ville knyttet feil tekst
        # til feil studie, så vi lagrer ingenting og lar batchen prøve igjen i morgen.
        print("✗  Klarte ikke å knytte svaret til noen studie — ingenting lagret.")
        return refused_dois

    today = datetime.now().date().isoformat()
    wrote = vraket = 0
    for e in batch_entries:
        if e.get("status") == "rejected":
            continue
        url = e.get("url")
        if url in blocks:
            e["status"] = "ready"
            e["writeup"] = blocks[url]
            e["writeup_at"] = today
            e.pop("abstract", None)  # teksten er skrevet — abstractet trengs ikke mer
            wrote += 1
        elif url in skipped:
            e["status"] = "rejected"
            e["reject_reason"] = "vraket av Claude"
            e.pop("abstract", None)
            vraket += 1
        else:
            # Verken omtalt eller eksplisitt vraket — Claude hoppet stille over den.
            # Én gang kan være et lengdekutt; skjer det gjentatte ganger, er studien
            # ubrukelig og skal ikke sendes inn igjen i det uendelige.
            e["passes"] = e.get("passes", 0) + 1
            if e["passes"] >= WRITEUP_MAX_PASSES:
                e["status"] = "rejected"
                e["reject_reason"] = f"hoppet over {e['passes']} ganger"
                e.pop("abstract", None)

    print(f"  ✓  {wrote} omtaler lagret i køen"
          + (f", {vraket} vraket av Claude" if vraket else ""))
    return refused_dois


def pop_for_today(queue: list[dict]) -> list[dict]:
    """Plukk dagens studier fra køen — de høyest scorede med ferdig tekst.

    Kategoritaket er MYKT: vi tar først opptil MAX_PER_CATEGORY per kategori, og fyller
    deretter opp med de nest beste uansett kategori hvis dagen ikke ble full. Uten taket kan
    fem kosthold-studier havne på samme dag (køen er sortert på score alene) og nettsiden
    vise én eneste gruppe; med et HARDT tak ville en ensidig kø gitt unødig korte dager."""
    ready = [e for e in queue if e.get("status") == "ready" and e.get("writeup")]
    picked: list[dict] = []
    per_cat: dict[str, int] = {}

    for e in ready:
        if len(picked) >= MAX_ITEMS:
            break
        cat = e.get("category") or "?"
        if per_cat.get(cat, 0) >= MAX_PER_CATEGORY:
            continue
        picked.append(e)
        per_cat[cat] = per_cat.get(cat, 0) + 1

    if len(picked) < MAX_ITEMS:  # mykt tak: fyll opp med det som er igjen
        chosen = {id(e) for e in picked}
        for e in ready:
            if len(picked) >= MAX_ITEMS:
                break
            if id(e) not in chosen:
                picked.append(e)

    return picked


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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fyll og prun køen uten å kalle Claude (koster ingenting). "
             "Publiserer fra det som allerede ligger ferdigskrevet i køen.",
    )
    args = parser.parse_args()

    _load_dotenv()

    if not os.environ.get("ANTHROPIC_API_KEY") and not args.dry_run:
        print("Feil: ANTHROPIC_API_KEY er ikke satt.")
        sys.exit(1)

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_human = datetime.now().strftime("%A %d. %B %Y")

    print(f"\n{'─'*70}")
    print(f"  Forskningsbriefing  —  {today_human}")
    print(f"{'─'*70}\n")

    today = datetime.now().date()
    seen = _load_seen()

    # ── 1–2. Last og prun køen ───────────────────────────────────────────────
    queue = _prune_queue(_load_queue(), seen, today)
    counts = _queue_counts(queue)
    print(f"Kø: {counts['ready']} ferdigskrevne, {counts['scored']} venter på tekst, "
          f"{counts['rejected']} vraket.")

    # ── 3. Påfylling fra Europe PMC — kun når køen er kort nok ───────────────
    if counts["scored"] >= QUEUE_REFILL_BELOW:
        print(f"  ⓘ  {counts['scored']} studier i kø (≥ {QUEUE_REFILL_BELOW}) — "
              "hopper over henting.")
    else:
        print(
            f"\nHenter forskning fra Europe PMC — menneskestudier "
            f"(RCT/metaanalyse/oversikt), siste {LOOKBACK_DAYS} dager..."
        )
        refill_queue(queue, seen, today)
        _save_queue(queue)
        counts = _queue_counts(queue)
        print(f"  → kø: {counts['ready']} ferdige, {counts['scored']} venter på tekst")

    # ── 4. Claude-omtaler i batch — kun når lageret av ferdige tekster er lavt ─
    refused_dois: list[str] = []
    if args.dry_run:
        print("\n  ⓘ  --dry-run: hopper over Claude-kallet.")
    elif counts["ready"] >= WRITEUP_REFILL_BELOW:
        print(f"\n  ⓘ  {counts['ready']} ferdigskrevne studier i kø "
              f"(≥ {WRITEUP_REFILL_BELOW}) — hopper over Claude-kallet. Gratis dag.")
    elif counts["scored"] == 0:
        print("\n  ⚠  Ingen studier i kø å skrive om.")
    else:
        refused_dois = write_up_batch(queue)
        _save_queue(queue)
        # Refused lagres UANSETT hvordan resten gikk — en refusal er deterministisk, og
        # uten flagget ville nøyaktig samme abstract kommet tilbake og betalt hele
        # isoler-og-fjern-runden på nytt.
        if refused_dois:
            _save_seen(seen, [], refused_dois)
            print(f"  ⓘ  {len(refused_dois)} avvist(e) abstract(s) merket refused — "
                  "sendes aldri inn igjen.")

    # ── 5. Publisering — aldri et API-kall ───────────────────────────────────
    picked_entries = pop_for_today(queue)
    if not picked_entries:
        # Myk feil, som øvrige seksjoner: feltet utelates for dagen. Vi skriver ALDRI en
        # tom research_md — det ville overskrevet dagsfilen og sett ut som en stille dag.
        print("\n✗  Ingen ferdigskrevne studier i køen — forskningsfeltet utelates i dag.")
        sys.exit(1)

    briefing = _STUDY_SEPARATOR.join(e["writeup"] for e in picked_entries)

    print(f"\nDagens briefing — {len(picked_entries)} studier fra køen:")
    for e in picked_entries:
        print(f"    {e.get('score', 0):5.1f}  [{e.get('category')}] {e['title'][:66]}")

    if args.dry_run:
        print("\n  ⓘ  --dry-run: publiserer ikke, og køen tømmes ikke.")
        sys.exit(0)

    # Vist = blokkert for godt. Oppføringene fjernes samtidig fra køen.
    _save_seen(seen, [e["doi"] for e in picked_entries if e.get("doi")], [])
    published = {id(e) for e in picked_entries}
    queue = [e for e in queue if id(e) not in published]
    _save_queue(queue)

    left = _queue_counts(queue)
    print(f"  → {left['ready']} ferdigskrevne igjen i kø "
          f"({left['ready'] // MAX_ITEMS} dager), {left['scored']} venter på tekst")

    # Lagre forskningsbriefingen til datalageret (merges inn i samme dagsfil som nyhetsbriefen)
    research_items = [
        {"title": e["title"], "url": e["url"], "journal": e.get("journal", "—"),
         "date": e.get("date", "—"), "category": e.get("category")}
        for e in picked_entries
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
