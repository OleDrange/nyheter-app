#!/usr/bin/env python3
"""
textile_briefing.py  —  Kunnskapsbase om tekstiler, hud og kvalitet

Bygger opp tekstil.modr.no: et voksende oppslagsverk over fibre, behandlinger,
kvalitetsmål og miljøpåvirkning, med forskningsevidens under hvert emne og en samlet
kravspesifikasjon for innkjøp.

Kjør:
    python textile_briefing.py             # full kjøring (bruker Claude-kvote)
    python textile_briefing.py --dry-run   # hent, score og køfyll uten Claude — gratis
    python textile_briefing.py --seed      # engangs: fyll køen og skriv ut mange emner

Systemet er KØBASERT og AKKUMULERENDE, ikke dagsbasert. Til forskjell fra
`research_briefing.py`, som publiserer en briefing per dag og deretter er ferdig med
studien, ruller dette systemet hver studie INN i ett eller flere emner som blir stående.
En kjøring er seks steg, der de tunge hoppes over når de ikke trengs:

    1. Last kunnskapsbase, kø og dedup-cache.
    2. Prun køen (for gamle, allerede innrullet).
    3. Påfyll fra Europe PMC + OpenAlex  — kun når køen er kort (QUEUE_REFILL_BELOW).
    4. Claude skriver omtaler i batch    — kun når lageret av ferdige tekster er lavt.
    5. Rull inn i kunnskapsbasen         — ren datamanipulasjon, aldri et API-kall.
    6. Syntetiser emner som fikk ny evidens — Claude, maks MAX_SYNTH_PER_RUN per kjøring.

To kilder, fordi ett fagfelt ikke dekker spørsmålet:
  • Europe PMC  — hud, allergi, toksikologi. Fagfellevurdert biomedisin.
  • OpenAlex    — tekstilteknikk, holdbarhet, LCA. Disse studiene finnes ikke i PMC i det
                  hele tatt; uten OpenAlex ville «kvalitet» og «miljø» stått permanent tomme.

Miljøvariabler (deles med news_briefing.py via .env):
    ANTHROPIC_API_KEY   — påkrevd (ikke for --dry-run)
    BRIEFING_DATA_DIR   — hvor kø/KB/dedup lagres (/data i container)
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import httpx
import anthropic

from news_briefing import _load_dotenv
from textile_topics import (
    TOPICS, TOPICS_BY_SLUG, KIND_LABELS, KIND_ORDER, VERDICTS, VERDICT_ORDER, STRENGTHS,
)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — juster her
# ─────────────────────────────────────────────────────────────────────────────

MODEL = "claude-opus-5"                # samme modell som de tre andre generatorene
MAX_TOKENS = 32000                   # 8 omtaler + Opus 5 sin tenking
SYNTH_MAX_TOKENS = 12000             # ett emnesammendrag + tenking

# Vindu på publiseringsdato. Tekstilkjemi har ingen nyhetssyklus i det hele tatt — en
# patch-test-serie fra 2023 er like gyldig som en fra i går, og feltet publiserer langt
# tregere enn longevity-forskningen. Vi kunne like gjerne hatt et femårsvindu; 730 dager er
# valgt så «status» i et emne alltid hviler på noe som er skrevet nylig nok til å ta hensyn
# til gjeldende REACH-restriksjoner.
LOOKBACK_DAYS = 730

PAGE_SIZE = 100
MAX_FETCH_PER_CATEGORY = 400         # sikkerhetsventil per spørring

# ── Køen ─────────────────────────────────────────────────────────────────────
QUEUE_FILE = "textile_queue.json"
KB_FILE = "textile_kb.json"
SEEN_FILE = "textile_seen.json"

QUEUE_REFILL_BELOW = 40    # under så mange «scored» i kø: hent nytt fra kildene
QUEUE_MAX_AGE_DAYS = 1100  # prun køoppføringer eldre enn dette (publiseringsdato)
SEEN_RETENTION_DAYS = 1500 # en innrullet studie skal aldri hentes inn igjen

WRITEUP_REFILL_BELOW = 8   # under så mange «ready» i kø: kall Claude
WRITEUP_BATCH_SIZE = 8     # omtaler per Claude-kall (MAX_TOKENS må følge med)
WRITEUP_MAX_PASSES = 2     # hoppet stille over så mange ganger → rejected

# Hvor mye kunnskapsbasen vokser per kjøring. Dette er den eneste knappen som styrer
# tempoet: alt annet (henting, scoring) er gratis og skjer uansett.
MAX_ROLLIN_PER_RUN = 5     # studier som rulles inn i KB-en per kjøring
MAX_SYNTH_PER_RUN = 3      # emner som re-syntetiseres per kjøring

# Et emne re-syntetiseres først når det har fått nok ny evidens til at teksten faktisk kan
# bli en annen. Uten terskelen ville hver eneste studie utløst et Claude-kall på et emne
# som allerede har 20 studier under seg, og teksten hadde knapt endret seg.
SYNTH_PENDING_MIN = 2      # antall nye studier før et emne står for tur
SYNTH_FIRST_MIN = 1        # …men et emne uten sammendrag i det hele tatt trenger bare én

MAX_ABSTRACT_CHARS = 3500  # maks tegn fra hvert abstract i prompten (aldri før scoring)

CLAUDE_MAX_ATTEMPTS = 3
CLAUDE_RETRY_DELAY = 5
CLAUDE_REFUSAL_MAX_ROUNDS = 4
CLAUDE_PROBE_MAX_TOKENS = 16

MIN_SCORE = 2.5            # under dette settes en studie aldri i kø

_UA = "tekstil-briefing/1.0 (https://tekstil.modr.no; personal research project)"


# ─────────────────────────────────────────────────────────────────────────────
# Spørringer — Europe PMC (hud, allergi, toksikologi)
#
# Til forskjell fra research_briefing.py krever vi IKKE RCT/metaanalyse her. Det er ikke
# slurv: tekstilallergi dokumenteres gjennom patch-test-serier, kohorter og
# eksponeringsmålinger, og det finnes knapt randomiserte forsøk på om en polyestergenser
# gir eksem. Krever man RCT, står emnene tomme. Kvalitetskravet flyttes i stedet til den
# lokale scoringen (tallsignaler, utvalgsstørrelse, studiedesign) og til Claudes
# **Forbehold**-avsnitt, som skal si hva designet IKKE kan vise.
#
# Emneordene er bundet til TITTELEN, av samme grunn som i research_briefing.py: fritekst
# ga en pool full av artikler der ordet «textile» dukket opp i én bisetning om
# sårbandasjer. Se CLAUDE.md.
# ─────────────────────────────────────────────────────────────────────────────

_PMC_SUFFIX = " AND SRC:MED AND LANG:eng AND HAS_ABSTRACT:Y"

PMC_QUERIES: dict[str, str] = {
    # Hudreaksjoner på plagg og tekstilkjemikalier
    "hud": (
        '((TITLE:"contact dermatitis" OR TITLE:"contact allergy" OR TITLE:"skin sensitization" '
        'OR TITLE:"skin sensitisation" OR TITLE:"patch test" OR TITLE:"eczema" '
        'OR TITLE:"atopic dermatitis" OR TITLE:"urticaria" OR TITLE:"skin irritation" '
        'OR TITLE:"skin barrier" OR TITLE:"skin microbiome")'
        ' AND (TITLE:"textile" OR TITLE:"clothing" OR TITLE:"garment" OR TITLE:"fabric" '
        'OR TITLE:"dye" OR TITLE:"disperse" OR TITLE:"formaldehyde" OR TITLE:"nickel" '
        'OR TITLE:"chromium" OR TITLE:"wool" OR TITLE:"cotton" OR TITLE:"polyester" '
        'OR TITLE:"silk" OR TITLE:"silver" OR TITLE:"leather" OR TITLE:"rubber" '
        'OR TITLE:"shoe" OR TITLE:"glove"))' + _PMC_SUFFIX
    ),
    # Tekstilallergener som gruppe — fanger patch-test-seriene som ikke nevner plagget
    "allergener": (
        '(TITLE:"textile dermatitis" OR TITLE:"textile allergy" OR TITLE:"disperse dye" '
        'OR TITLE:"azo dye" OR TITLE:"textile dye" OR TITLE:"para-phenylenediamine" '
        'OR TITLE:"nickel release" OR TITLE:"nickel allergy" OR TITLE:"chromium allergy" '
        'OR TITLE:"hexavalent chromium" OR TITLE:"optical brightener" '
        'OR TITLE:"formaldehyde releaser")' + _PMC_SUFFIX
    ),
    # Kjemikalier i eller fra plagg — eksponering og toksikologi
    "kjemikalier": (
        '((TITLE:"PFAS" OR TITLE:"perfluoroalkyl" OR TITLE:"polyfluoroalkyl" '
        'OR TITLE:"flame retardant" OR TITLE:"organophosphate ester" OR TITLE:"phthalate" '
        'OR TITLE:"bisphenol" OR TITLE:"nonylphenol" OR TITLE:"alkylphenol" '
        'OR TITLE:"triclosan" OR TITLE:"silver nanoparticle" OR TITLE:"quaternary ammonium" '
        'OR TITLE:"antimony" OR TITLE:"aromatic amine" OR TITLE:"benzothiazole")'
        ' AND (TITLE:"textile" OR TITLE:"clothing" OR TITLE:"garment" OR TITLE:"fabric" '
        'OR TITLE:"apparel" OR TITLE:"consumer product" OR TITLE:"dermal" '
        'OR TITLE:"skin" OR TITLE:"exposure" OR TITLE:"house dust" OR TITLE:"indoor"))'
        + _PMC_SUFFIX
    ),
    # Mikroplast/mikrofiber med helsevinkling (miljøvinklingen tas av OpenAlex)
    "mikrofiber_helse": (
        '((TITLE:"microplastic" OR TITLE:"microfibre" OR TITLE:"microfiber" '
        'OR TITLE:"nanoplastic")'
        ' AND (TITLE:"human" OR TITLE:"health" OR TITLE:"inhalation" OR TITLE:"lung" '
        'OR TITLE:"dermal" OR TITLE:"indoor" OR TITLE:"house dust" OR TITLE:"textile" '
        'OR TITLE:"clothing"))' + _PMC_SUFFIX
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# Spørringer — OpenAlex (tekstilteknikk, kvalitet, miljø)
#
# OpenAlex-syntaksen er `title.search:(A OR B) AND (C OR D)`. Vi binder også her til
# tittelen: `search`-parameteren (fritekst med relevansrangering) ga en pool av
# materialforskning der ett plagg-ord i et abstract om EMI-skjerming holdt.
# ─────────────────────────────────────────────────────────────────────────────

OPENALEX_QUERIES: dict[str, str] = {
    "holdbarhet": (
        'title.search:(abrasion OR pilling OR "tensile strength" OR "tear strength" '
        'OR durability OR "wear resistance" OR martindale OR "seam strength" '
        'OR "colour fastness" OR "color fastness" OR "wash fastness" OR "dimensional stability" '
        'OR shrinkage OR "service life" OR longevity) '
        'AND (fabric OR textile OR garment OR apparel OR clothing OR yarn OR knit OR woven)'
    ),
    "prosess": (
        'title.search:(dyeing OR finishing OR mercerization OR mercerisation OR bleaching '
        'OR scouring OR tanning OR "wet processing" OR softener OR coating) '
        'AND (cotton OR wool OR textile OR fabric OR leather OR denim OR polyester)'
    ),
    "miljo": (
        'title.search:("life cycle assessment" OR "environmental impact" OR "carbon footprint" '
        'OR "water footprint" OR circularity OR recycling OR biodegradation OR biodegradability '
        'OR wastewater OR effluent OR "microfibre release" OR "microfiber release" '
        'OR "fibre shedding" OR "fiber shedding") '
        'AND (textile OR apparel OR clothing OR garment OR fabric OR denim)'
    ),
    "komfort": (
        'title.search:("moisture management" OR wicking OR "air permeability" OR breathability '
        'OR "thermal comfort" OR "thermal resistance" OR "skin contact" OR friction OR softness '
        'OR "next-to-skin") '
        'AND (fabric OR textile OR garment OR clothing OR knit)'
    ),
}

_PMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_OA_API = "https://api.openalex.org/works"
_HEADERS = {"User-Agent": _UA}


# ─────────────────────────────────────────────────────────────────────────────
# Prompter
# ─────────────────────────────────────────────────────────────────────────────

def _topic_menu() -> str:
    """Emnelista slik Claude ser den. Bygges fra registeret, så et nytt emne i
    textile_topics.py blir automatisk tilgjengelig for tildeling."""
    lines = []
    for kind in KIND_ORDER:
        lines.append(f"\n{KIND_LABELS[kind].upper()}:")
        for t in TOPICS:
            if t["kind"] == kind:
                lines.append(f"  {t['slug']} — {t['name']}: {t['blurb']}")
    return "\n".join(lines)


WRITEUP_SYSTEM_PROMPT = """Du bygger et fagoppslagsverk på norsk om tekstiler, hud og kvalitet. Leseren planlegger å starte en klesbutikk som skal selge plagg som er bedre for kroppen og varer lenger. Han er ikke tekstilingeniør, men han er analytisk og vil ha tall. Alt du skriver skal til slutt kunne oversettes til et krav han kan stille en leverandør.

Du får en liste med forskningsartikler (kildekategori, tittel, tidsskrift, dato, URL, engelsk sammendrag). Utvalget er allerede gjort av en lokal scoring — du skal ikke velge mellom dem, du skal skrive om hver av dem.

VRAKING: er en artikkel likevel ubrukelig, skal du IKKE skrive en omtale. Skriv i stedet nøyaktig denne linjen, alene på en linje:
## SKIP [n] — kort begrunnelse
der [n] er artikkelens nummer i listen. Vrak kun når ett av disse er oppfylt:
1. Den handler ikke egentlig om klær, tekstiler eller skinn — ordet dukket bare opp i en bisetning.
2. Den er ren materialforskning uten relevans for plagg man går med (sensorer, EMI-skjerming, batterier, medisinske implantater, filtermembraner, byggematerialer).
3. Den har ingen konkrete funn i det hele tatt — bare en programerklæring eller en ren metodebeskrivelse.
Vraking skal være unntaket. Er du i tvil, skriv omtalen.

EMNETILDELING: hver omtale skal knyttes til 1–3 emner fra listen under. Bruk de eksakte slug-navnene. Velg det studien FAKTISK handler om — er den om PFAS-impregnering på bomullsjakker, er emnene `pfas` og eventuelt `bomull`; er den om nikkelfrigjøring fra glidelåser, er den `tungmetaller`. Ikke strø om deg med emner for å treffe bredt; et emne med presis evidens er verdt mer enn fem med løs.

TILGJENGELIGE EMNER:{TOPIC_MENU}

FORMAT — for hver artikkel du skriver om, nøyaktig denne strukturen:
## [Norsk tittel som bærer hovedfunnet](URL)
**Emner:** slug1, slug2
**Metode:** Hva slags studie er dette, hva ble undersøkt, på hvor mange (deltakere, plagg, prøver, vaskesykluser), og hvordan ble det målt? Nevn standarden hvis den er oppgitt (ISO, AATCC, Martindale, patch-test-serie). Forklar designet slik at leseren skjønner hvor tungt funnet veier. 3–4 setninger.
**Funn:** Hovedresultatene med konkrete tall — prosentandeler, konsentrasjoner (µg/g, ppm), antall sykluser, OR/RR med konfidensintervall, p-verdier der de finnes. Si alltid hva det ble sammenlignet MOT. 3–4 setninger.
**Hva det betyr for innkjøp:** Oversett funnet til en konkret konsekvens for et plagg som skal selges. Hvilken fiber, behandling eller sertifisering peker dette mot eller bort fra? Hvilket spørsmål bør stilles en leverandør, eller hvilken testrapport bør kreves? Vær konkret nok til at setningen kan stå i en kravspesifikasjon. 3–4 setninger.
**Forbehold:** Hva studien IKKE viser. Målt i laboratorium og ikke på hud; små prøvetall; ekstraksjon med kunstig svette sier ikke hva som faktisk trenger gjennom huden; en patch-test-serie på hudpoliklinikk overrepresenterer allergikere; en LCA er følsom for systemgrensene. 1–2 setninger.

REGLER:
- Tittelen skal si HVA studien fant — retning og tall der de finnes. Godt: «Dispersjonsfarger ble påvist i 43 % av testede polyesterplagg». Dårlig: «Studie om fargestoffer i tekstiler».
- Bruk ALLTID den oppgitte URL-en i lenken, uendret.
- Oversett til norsk, men behold faguttrykk der de er presise (patch-test, Martindale, LCA, konfidensintervall).
- Forklar forkortelser og måleenheter første gang de brukes.
- Ikke overdriv funn utover det sammendraget støtter, og ikke dikt opp tall.
- Ingen fyllord, ingen innledning, ingen oppsummering — start rett på første ## artikkel."""


SYNTH_SYSTEM_PROMPT = """Du vedlikeholder ett oppslag i et norsk fagoppslagsverk om tekstiler, hud og kvalitet. Leseren planlegger en klesbutikk med plagg som skal være bedre for kroppen og vare lenger, og bruker oppslaget som beslutningsgrunnlag for innkjøp.

Du får emnets navn, en kort beskrivelse, eventuelt det forrige sammendraget, og all evidensen som ligger under emnet (omtaler av forskningsartikler skrevet tidligere). Skriv oppslaget på nytt i sin helhet, basert på ALL evidensen — ikke bare det nye.

Svar med ETT JSON-objekt og ingenting annet. Ingen kodeblokk, ingen forklaring rundt. Formen er:

{
  "summary": "<markdown, 2–4 avsnitt>",
  "verdict": "<unngaa | dokumenter | foretrekk | noeytral | ukjent>",
  "confidence": "<sterk | moderat | svak>",
  "criteria": [
    {"level": "<unngaa | dokumenter | foretrekk>", "text": "<ett konkret innkjøpskrav>", "strength": "<sterk | moderat | svak>"},
    ...
  ],
  "questions": ["<konkret spørsmål å stille en leverandør>", ...]
}

FELTENE:
- `summary`: Selve oppslaget. Første avsnitt sier hva saken er og hvor evidensen står i dag. Deretter det som faktisk er målt, med tall. Til slutt hva som er uavklart. Bruk **fet** på nøkkeltall og faguttrykk der det hjelper lesbarheten. Ikke gjenta omtalene ordrett — syntetiser, og si det når studiene spriker. Maks 350 ord.
- `verdict`: Emnets innkjøpsdom.
    unngaa    — evidensen tilsier at dette holdes ute av sortimentet
    dokumenter— kan brukes, men bare med dokumentasjon eller testrapport
    foretrekk — dette er et aktivt godt valg
    noeytral  — ingen innvending, men heller ikke noe fortrinn
    ukjent    — for tynt grunnlag til å konkludere. Bruk denne når evidensen er det.
- `confidence`: Hvor tungt evidensgrunnlaget veier SAMLET (antall studier, design, hvor entydige de er).
- `criteria`: 0–4 krav som kan stå ordrett i en kravspesifikasjon. Konkret og etterprøvbart: «Ingen plagg med DWR-behandling basert på side-kjede-fluorerte polymerer» er et krav; «Vi bør tenke på kjemikalier» er det ikke. Tallfest der evidensen tillater det. `strength` er hvor godt akkurat DETTE kravet er dekket av evidensen. Har emnet for tynt grunnlag, returner en tom liste — det er et gyldig svar.
- `questions`: 0–3 spørsmål å stille en fabrikk eller leverandør, eller testrapporter å be om (oppgi standardnummer når det er kjent: ISO 105-E04, ISO 12947, OEKO-TEX Standard 100, AATCC 61). Tom liste er gyldig.

REGLER:
- Bygg kun på evidensen du får. Ikke hent inn påstander utenfra, og ikke dikt opp tall eller standardnummer du ikke er sikker på.
- Er evidensen tynn eller motstridende, SI det, og sett `verdict` til `ukjent`. Et ærlig «vi vet ikke» er mer verdt enn en anbefaling som ikke bærer.
- Skill mellom fiberen selv og det som er gjort med den. Bomull er ikke problemet; harpiksen på bomullen kan være det.
- Norsk. Ingen fyllord."""


# ─────────────────────────────────────────────────────────────────────────────
# Lagring — tre filer, alle i BRIEFING_DATA_DIR (MÅ persisteres, se CLAUDE.md)
#
#   textile_kb.json     — kunnskapsbasen. Det eneste nettsiden leser.
#   textile_queue.json  — studier vurdert, men ennå ikke rullet inn.
#   textile_seen.json   — {id: {"last": dato, "rolled": bool, "refused": bool}}
#
# Mister du køen, bygges den opp igjen ved neste kjøring — men Claude-omtalene i den er
# betalt for. Mister du KB-en, er ALT tapt: den er summen av hver omtale og hver syntese
# systemet noen gang har skrevet, og kan ikke regenereres uten å betale for alt på nytt.
# ─────────────────────────────────────────────────────────────────────────────


def _data_path(filename: str) -> str:
    base = os.environ.get("BRIEFING_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, filename)


def _write_json(path: str, payload) -> None:
    """Atomisk skriving (.tmp + os.replace), som store_briefing()."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        print(f"  ⚠  Kunne ikke skrive {os.path.basename(path)}: {exc}")


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ── Dedup-cache ──────────────────────────────────────────────────────────────

def _load_seen() -> dict:
    raw = _read_json(_data_path(SEEN_FILE), {}) or {}
    seen = {}
    for key, val in raw.items():
        if isinstance(val, str):
            seen[key] = {"last": val, "rolled": True, "refused": False}
        elif isinstance(val, dict) and val.get("last"):
            seen[key] = {
                "last": val["last"],
                "rolled": bool(val.get("rolled")),
                "refused": bool(val.get("refused")),
            }
    return seen


def _is_blocked(seen: dict, key: str, today: date | None = None) -> bool:
    """Skal denne studien holdes ute av køen? Kun to varige grunner: den er allerede
    rullet inn i kunnskapsbasen, eller den ble avvist av sikkerhetsklassifikatoren
    (deterministisk — nytt forsøk koster bare en ny bisect-runde)."""
    entry = seen.get(key)
    if not entry or not (entry["rolled"] or entry.get("refused")):
        return False
    cutoff = ((today or datetime.now().date()) - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    return entry["last"] >= cutoff


def _save_seen(seen: dict, rolled: list[str], refused: list[str] | None = None) -> None:
    today = datetime.now().date().isoformat()
    rolled_s, refused_s = set(filter(None, rolled)), set(filter(None, refused or []))
    for key in rolled_s | refused_s:
        prev = seen.get(key, {})
        seen[key] = {
            "last": today,
            "rolled": key in rolled_s or prev.get("rolled", False),
            "refused": key in refused_s or prev.get("refused", False),
        }
    keep_from = (datetime.now().date() - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v["last"] >= keep_from}
    _write_json(_data_path(SEEN_FILE), seen)


# ── Køen ─────────────────────────────────────────────────────────────────────

def _load_queue() -> list[dict]:
    raw = _read_json(_data_path(QUEUE_FILE), {}) or {}
    return [e for e in (raw.get("entries") or []) if isinstance(e, dict) and e.get("id")]


def _save_queue(queue: list[dict]) -> None:
    queue.sort(key=lambda e: e.get("score", 0.0), reverse=True)
    _write_json(_data_path(QUEUE_FILE), {
        "version": 1,
        "updated": datetime.now().date().isoformat(),
        "entries": queue,
    })


def _queue_counts(queue: list[dict]) -> dict[str, int]:
    counts = {"scored": 0, "ready": 0, "rejected": 0}
    for e in queue:
        st = e.get("status", "scored")
        counts[st] = counts.get(st, 0) + 1
    return counts


def _prune_queue(queue: list[dict], seen: dict, today: date) -> list[dict]:
    cutoff = (today - timedelta(days=QUEUE_MAX_AGE_DAYS)).isoformat()
    kept, old, done = [], 0, 0
    for e in queue:
        if (e.get("date") or "")[:10] and e["date"][:10] < cutoff:
            old += 1
            continue
        if _is_blocked(seen, e["id"], today):
            done += 1
            continue
        kept.append(e)
    if old or done:
        print(f"  ⓘ  prunet kø: {old} for gamle, {done} allerede innrullet")
    return kept


# ── Kunnskapsbasen ───────────────────────────────────────────────────────────

def _load_kb() -> dict:
    """Les KB-en og synkroniser den mot emneregisteret.

    Emner som er lagt til i textile_topics.py siden sist opprettes tomme; emner som er
    FJERNET fra registeret blir stående i KB-en med evidensen sin (vi sletter aldri noe
    som er betalt for), men markeres `retired` så nettsiden kan tone dem ned."""
    kb = _read_json(_data_path(KB_FILE), None) or {}
    topics = kb.get("topics") or {}
    studies = kb.get("studies") or {}

    for t in TOPICS:
        cur = topics.get(t["slug"]) or {}
        cur.update({
            "slug": t["slug"], "name": t["name"], "kind": t["kind"], "blurb": t["blurb"],
            "retired": False,
        })
        cur.setdefault("summary", "")
        cur.setdefault("verdict", "ukjent")
        cur.setdefault("confidence", "svak")
        cur.setdefault("criteria", [])
        cur.setdefault("questions", [])
        cur.setdefault("studies", [])     # liste av studie-id-er, nyeste først
        cur.setdefault("pending", 0)      # ny evidens siden siste syntese
        cur.setdefault("updated", None)
        cur.setdefault("synth_at", None)
        topics[t["slug"]] = cur

    for slug, cur in topics.items():
        if slug not in TOPICS_BY_SLUG:
            cur["retired"] = True

    kb.update({
        "version": 1,
        "topics": topics,
        "studies": studies,
        "kind_labels": KIND_LABELS,
        "kind_order": KIND_ORDER,
        "verdicts": VERDICTS,
        "verdict_order": VERDICT_ORDER,
        "strengths": STRENGTHS,
    })
    return kb


def _save_kb(kb: dict) -> None:
    kb["updated"] = datetime.now().date().isoformat()
    kb["study_count"] = len(kb.get("studies") or {})
    _write_json(_data_path(KB_FILE), kb)


# ─────────────────────────────────────────────────────────────────────────────
# Emnetildeling — gratis og deterministisk, før Claude ser noe som helst
# ─────────────────────────────────────────────────────────────────────────────

def assign_topics(title: str, abstract: str) -> tuple[list[str], list[str]]:
    """Knytt en artikkel til emner ut fra søkeordene i registeret.

    Returnerer (primære, sekundære): treff i TITTELEN er primært (studien handler om
    emnet), treff kun i sammendraget er sekundært (emnet er nevnt). Skillet brukes av
    scoringen — en artikkel uten et eneste primært emne er nesten alltid en artikkel som
    tilfeldigvis nevner et tekstilord, og den skal ikke i kø."""
    t, a = title.lower(), abstract.lower()
    primary, secondary = [], []
    for topic in TOPICS:
        if any(term in t for term in topic["terms"]):
            primary.append(topic["slug"])
        elif any(term in a for term in topic["terms"]):
            secondary.append(topic["slug"])
    return primary, secondary


# ─────────────────────────────────────────────────────────────────────────────
# Lokal scoring
#
# Kildene er brede med vilje (vi kan ikke spørre per emne — «formaldehyde AND textile» gir
# 6 treff på to år), så scoringen er der utvalget faktisk skjer. Den rangerer på: er dette
# om plagg man går med, er det målt på noe, og er det tall å skrive «Funn» av?
# ─────────────────────────────────────────────────────────────────────────────

# Tall og effektmål — samme rolle som i research_briefing.py, men utvidet med de
# måleenhetene tekstilkjemi og materialprøving faktisk rapporterer i.
_STATS_PATTERNS = [
    r"\b95\s*%?\s*ci\b",
    r"\b(odds ratio|or)\s*[:=]?\s*\d",
    r"\b(risk ratio|relative risk|rr)\s*[:=]?\s*\d",
    r"\b(hazard ratio|hr)\s*[:=]?\s*\d",
    r"\bp\s*[<=>]\s*0?\.\d",
    r"\b\d{1,3}(\.\d+)?\s*%",
    r"\b\d+(\.\d+)?\s*(µg|ug|mg|ng|ppm|ppb)\s*/?\s*(g|kg|l|ml|m2|m²|cm2)?",
    r"\b\d+\s*(cycles|rubs|washes|washing cycles|laundering cycles)",
    r"\bgrade\s*[0-9](\.\d)?\b",              # fargeekthet: grad 1–5
    r"\bn\s*=\s*\d",
]

# Det studien må handle om for å høre hjemme her: et plagg man går med.
_APPAREL_TERMS = [
    "textile", "clothing", "garment", "apparel", "fabric", "fibre", "fiber", "yarn",
    "knit", "woven", "denim", "wool", "cotton", "polyester", "viscose", "linen",
    "leather", "shoe", "footwear", "sock", "underwear", "t-shirt", "jacket", "wear",
    "laundering", "washing", "dye", "clothes",
]

# Materialforskningens støy. Disse artiklene handler om stoffer i laboratoriet — sensorer,
# skjerming, batterier, katalyse — og aldri om noe man kan kjøpe og gå med. De dominerer
# OpenAlex-poolen fullstendig hvis de ikke straffes: den første testkjøringen ga
# «MXene-basert multimodal EMI-skjerming» som toppresultat på en spørring om slitestyrke.
_LAB_NOISE = [
    "emi shielding", "electromagnetic interference", "supercapacitor", "battery",
    "triboelectric", "piezoelectric", "sensor", "wearable electronic", "e-textile",
    "photocatal", "catalytic degradation", "adsorbent", "adsorption of", "membrane filtration",
    "drug delivery", "wound dressing", "tissue engineering", "scaffold", "implant",
    "self-healing", "superhydrophobic", "mxene", "graphene", "aerogel", "nanogenerator",
    "solar", "thermoelectric", "phase change material", "electrospun", "electrospinning",
    "composite laminate", "concrete", "geotextile", "ballistic", "aerospace",
    # Ren materialsyntese. «Solvent-Free Synthesis of a Phosphorus-Based Flame Retardant»
    # er kjemi om et molekyl, ikke om et plagg — den lå på 7.-plass i køen før dette.
    "synthesis of", "solvent-free synthesis", "fabrication of", "preparation of",
    "facile preparation", "novel coating", "in situ growth", "grafting of",
]

# Miljøstudier om hvor forurensningen HAVNER. Faglig gode, men de svarer på et annet
# spørsmål enn vårt: vi skal velge et plagg, ikke kartlegge en innsjø. «Global patterns of
# lake microplastic pollution» kom på 2. plass i den første testkjøringen fordi den treffer
# emnet `mikrofiberutslipp` og er full av tall.
_OFF_TARGET = [
    "lake", "river", "marine", "seawater", "ocean", "estuar", "sediment", "groundwater",
    "drinking water", "atmospheric deposition", "wastewater treatment plant", "sludge",
    "fish", "mussel", "zooplankton", "aquatic organism", "soil microplastic", "landfill",
    "agricultural", "food packaging", "bottled water",
]

# Design/robusthet — grovt, på abstractet.
_DESIGN_POINTS = [
    ("systematic review", 3.0),
    ("meta-analysis", 3.5),
    ("randomized", 2.5),
    ("cohort", 2.0),
    ("cross-sectional", 1.2),
    ("case-control", 1.5),
    ("patch test", 2.0),      # gullstandarden for kontaktallergi
    ("market survey", 1.5),
    ("life cycle assessment", 1.5),
]

# Direkte relevante utfall — det leseren skal kunne handle på.
_OUTCOME_TERMS = [
    "contact dermatitis", "contact allergy", "sensitization", "sensitisation",
    "patch test", "eczema", "skin irritation", "skin barrier", "urticaria",
    "dermal exposure", "dermal absorption", "migration", "release", "residual",
    "concentration", "abrasion", "pilling", "colour fastness", "color fastness",
    "tensile", "shrinkage", "durability", "service life", "shedding", "microfibre",
    "microfiber", "biodegradation", "recycl", "water consumption", "effluent",
]

_N_PATTERNS = [
    r"\bn\s*=\s*([\d,\. ]{2,12})",
    r"([\d,\. ]{2,12})\s*(participants|patients|subjects|individuals|samples|specimens|"
    r"garments|products|items|textiles|articles)",
    r"(?:including|involving|comprising|tested)\s+([\d,\. ]{2,12})\s",
]


def _extract_sample_size(text: str) -> int:
    best = 0
    for pat in _N_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            raw = re.sub(r"[,\. ]", "", m.group(1) or "")
            if raw.isdigit():
                n = int(raw)
                if 5 <= n <= 5_000_000:
                    best = max(best, n)
    return best


def _score_candidate(article: dict) -> tuple[float, str]:
    """Rangér en kandidat. Returnerer (score, kort begrunnelse for terminalloggen)."""
    title = article["title"].lower()
    abstract = article["abstract"].lower()
    text = f"{title} {abstract}"
    score, why = 0.0, []

    # Emnetreff er inngangsbilletten. Uten et primært emne er dette nesten alltid en
    # artikkel som bare nevner et tekstilord i forbifarten.
    primary = article.get("topics_primary") or []
    secondary = article.get("topics_secondary") or []
    if primary:
        score += min(2.0 + 0.5 * (len(primary) - 1), 3.5)
        why.append(f"{len(primary)} emne")
    else:
        score -= 2.5
        why.append("−intet primæremne")
    if secondary:
        score += min(0.3 * len(secondary), 1.0)

    # Handler den om et plagg?
    apparel_title = sum(1 for t in _APPAREL_TERMS if t in title)
    if apparel_title:
        score += min(0.8 * apparel_title, 2.0)
        why.append("plaggnær")
    elif not any(t in abstract for t in _APPAREL_TERMS):
        score -= 3.0
        why.append("−ikke om tekstil")

    for name, pts in _DESIGN_POINTS:
        if name in text:
            score += pts
            why.append(name.split()[0])
            break

    n = _extract_sample_size(text)
    if n:
        score += min(0.6 * math.log10(n), 2.5)
        why.append(f"n≈{n:,}".replace(",", " "))

    stats_hits = sum(1 for pat in _STATS_PATTERNS if re.search(pat, text))
    if stats_hits:
        score += min(0.8 * stats_hits, 3.0)
        why.append(f"{stats_hits} tallsignal")
    else:
        score -= 2.5  # ingen tall → ingenting å skrive «Funn» av
        why.append("−ingen tall")

    outcome_hits = sum(1 for t in _OUTCOME_TERMS if t in text)
    if outcome_hits:
        score += min(0.5 * outcome_hits, 2.5)

    # Laboratoriestøy: straffes hardt på tittelen, mildt i abstractet.
    noise_title = sum(1 for t in _LAB_NOISE if t in title)
    if noise_title:
        score -= 5.0 * noise_title
        why.append(f"−labstøy×{noise_title}")
    elif any(t in abstract for t in _LAB_NOISE):
        score -= 1.0

    off_title = sum(1 for t in _OFF_TARGET if t in title)
    if off_title:
        score -= 4.0 * off_title
        why.append(f"−annet felt×{off_title}")

    # Sitater sier noe om at feltet selv mener artikkelen betyr noe (kun OpenAlex).
    cites = article.get("cited_by") or 0
    if cites:
        score += min(0.4 * math.log10(cites + 1), 1.0)

    return score, ", ".join(why)


# ─────────────────────────────────────────────────────────────────────────────
# Henting — Europe PMC og OpenAlex, normalisert til samme artikkel-dict
# ─────────────────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _fetch_pmc(query: str) -> list[dict]:
    """Paginer gjennom Europe PMC via cursorMark. Myk feil: en feil på side 2+ kaster
    ikke bort sidene vi allerede har."""
    out, cursor = [], "*"
    while len(out) < MAX_FETCH_PER_CATEGORY:
        params = {
            "query": query, "resultType": "core", "sort": "P_PDATE_D desc",
            "pageSize": str(PAGE_SIZE), "format": "json", "cursorMark": cursor,
        }
        try:
            resp = httpx.get(_PMC_API, params=params, headers=_HEADERS, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            if out:
                break
            raise
        results = data.get("resultList", {}).get("result", [])
        out.extend(results)
        nxt = data.get("nextCursorMark")
        if not results or not nxt or nxt == cursor:
            break
        cursor = nxt
    return out


def _normalize_pmc(r: dict, category: str) -> dict | None:
    title = (r.get("title") or "").strip().rstrip(".")
    abstract = _strip_html(r.get("abstractText", ""))
    if not title or len(abstract) < 200:
        return None
    doi = (r.get("doi") or "").strip().lower()
    src, pid = r.get("source", ""), r.get("id", "")
    if doi:
        url = f"https://doi.org/{doi}"
    elif src and pid:
        url = f"https://europepmc.org/article/{src}/{pid}"
    else:
        return None
    journal = (
        (r.get("journalInfo") or {}).get("journal", {}).get("title")
        or r.get("journalTitle") or "—"
    )
    return {
        "id": doi or f"pmc:{src}/{pid}",
        "doi": doi,
        "url": url,
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "date": (r.get("firstPublicationDate") or "—")[:10],
        "source": "pmc",
        "category": category,
        "cited_by": 0,
    }


def _oa_abstract(inverted: dict | None) -> str:
    """OpenAlex leverer abstracts som invertert indeks (ord → posisjoner) fordi rå
    abstract-tekst er opphavsrettsbeskyttet i mange tidsskrifter. Vi setter den sammen
    igjen — det er samme tekst, bare lagret annerledes."""
    if not inverted:
        return ""
    slots: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        for p in positions:
            slots.append((p, word))
    slots.sort()
    return _strip_html(" ".join(w for _, w in slots))


def _fetch_openalex(filter_q: str, from_date: str) -> list[dict]:
    """Paginer OpenAlex via cursor. Sortert på siteringer: feltet publiserer mye, og de
    mest siterte artiklene i et toårsvindu er en bedre grovsortering enn de nyeste."""
    out, cursor = [], "*"
    select = ("id,doi,title,publication_date,cited_by_count,primary_location,"
              "abstract_inverted_index,type,language")
    while len(out) < MAX_FETCH_PER_CATEGORY:
        params = {
            "filter": f"{filter_q},from_publication_date:{from_date},"
                      "type:article,has_abstract:true,language:en",
            "per-page": str(min(PAGE_SIZE, 200)),
            "sort": "cited_by_count:desc",
            "cursor": cursor,
            "select": select,
            "mailto": "oledrange2@gmail.com",
        }
        try:
            resp = httpx.get(_OA_API, params=params, headers=_HEADERS, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            if out:
                break
            raise
        results = data.get("results") or []
        out.extend(results)
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not results or not cursor:
            break
        time.sleep(1)  # OpenAlex svarer 429 på raske serier — vær høflig
    return out


def _normalize_openalex(w: dict, category: str) -> dict | None:
    title = (w.get("title") or "").strip().rstrip(".")
    abstract = _oa_abstract(w.get("abstract_inverted_index"))
    if not title or len(abstract) < 200:
        return None
    doi = (w.get("doi") or "").replace("https://doi.org/", "").strip().lower()
    oa_id = (w.get("id") or "").rsplit("/", 1)[-1]
    if not doi and not oa_id:
        return None
    source = ((w.get("primary_location") or {}).get("source") or {})
    return {
        "id": doi or f"openalex:{oa_id}",
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else f"https://openalex.org/{oa_id}",
        "title": title,
        "abstract": abstract,
        "journal": source.get("display_name") or "—",
        "date": (w.get("publication_date") or "—")[:10],
        "source": "openalex",
        "category": category,
        "cited_by": w.get("cited_by_count") or 0,
    }


def _insert_scored(queue: list[dict], article: dict, score: float, why: str) -> bool:
    """Sett en nyscoret artikkel inn i køen. False hvis id-en allerede står der.
    Sorteringen skjer i _save_queue(), så plassering etter score faller ut av seg selv."""
    if any(e["id"] == article["id"] for e in queue):
        return False
    queue.append({
        **{k: article[k] for k in
           ("id", "doi", "url", "title", "journal", "date", "source", "category",
            "abstract", "cited_by")},
        "topics_primary": article.get("topics_primary") or [],
        "topics_secondary": article.get("topics_secondary") or [],
        "score": round(score, 2),
        "score_why": why,
        "queued_at": datetime.now().date().isoformat(),
        "status": "scored",
        "passes": 0,
        "writeup": None,
        "topics": None,        # settes av Claude ved omtale (**Emner:**)
        "writeup_at": None,
    })
    return True


def refill_queue(queue: list[dict], seen: dict, today: date) -> int:
    """Hent fra begge kildene, tildel emner lokalt, score, og sett alt over MIN_SCORE i kø.

    Ingen dagskvote: køen er ikke et dagsutvalg, den er et reservoar. Tempoet styres av
    MAX_ROLLIN_PER_RUN ved innrulling, som er riktig sted."""
    from_date = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    pmc_dates = f" AND (FIRST_PDATE:[{from_date} TO {today.isoformat()}])"

    batch_ids: set[str] = set()
    stats = {"seen": 0, "weak": 0, "dupe": 0, "thin": 0}
    inserted = 0

    sources: list[tuple[str, str, str]] = (
        [("pmc", cat, q + pmc_dates) for cat, q in PMC_QUERIES.items()]
        + [("openalex", cat, q) for cat, q in OPENALEX_QUERIES.items()]
    )

    for i, (kind, category, query) in enumerate(sources):
        if i:
            time.sleep(1)  # høflig mot begge API-ene
        try:
            raw = _fetch_pmc(query) if kind == "pmc" else _fetch_openalex(query, from_date)
        except Exception as exc:
            print(f"  ✗  {kind}/{category}: feil ved henting — {exc}")
            continue

        cat_inserted = 0
        for r in raw:
            article = (_normalize_pmc(r, category) if kind == "pmc"
                       else _normalize_openalex(r, category))
            if not article:
                stats["thin"] += 1
                continue
            if article["id"] in batch_ids:
                continue  # samme artikkel traff en tidligere spørring
            batch_ids.add(article["id"])
            if _is_blocked(seen, article["id"], today):
                stats["seen"] += 1
                continue

            primary, secondary = assign_topics(article["title"], article["abstract"])
            article["topics_primary"] = primary
            article["topics_secondary"] = secondary

            score, why = _score_candidate(article)
            if score < MIN_SCORE:
                stats["weak"] += 1
                continue
            if _insert_scored(queue, article, score, why):
                cat_inserted += 1
            else:
                stats["dupe"] += 1

        inserted += cat_inserted
        print(f"  ✓  {kind}/{category}: {cat_inserted} nye i kø (av {len(raw)} hentet)")

    notes = [
        (stats["seen"], "allerede innrullet"),
        (stats["dupe"], "sto allerede i køen"),
        (stats["thin"], "manglet tittel/sammendrag"),
        (stats["weak"], f"under terskel {MIN_SCORE}"),
    ]
    for n, label in notes:
        if n:
            print(f"      ({n} {label})")
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Claude — omtaler i batch
#
# Feilhåndteringen speiler research_briefing.py, og av samme grunn: en tom stream er
# enten en transient hikke (prøv igjen) eller en sikkerhets-refusal (deterministisk —
# isolér problemabstractet med billige prober og kjør uten det).
# ─────────────────────────────────────────────────────────────────────────────

def _writeup_system_prompt() -> str:
    return WRITEUP_SYSTEM_PROMPT.replace("{TOPIC_MENU}", _topic_menu())


def _build_user_content(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        hint = ", ".join(a.get("topics_primary") or []) or "—"
        lines.append(
            f"[{i}] ({a.get('category', '?')} / {a.get('source', '?')}) {a['title']}\n"
            f"Tidsskrift: {a['journal']} | Publisert: {a['date']}\n"
            f"URL: {a['url']}\n"
            f"Emneforslag fra lokal analyse (vurder selv): {hint}\n"
            f"Sammendrag: {a['abstract'][:MAX_ABSTRACT_CHARS]}\n"
            "---"
        )
        
    return (
        f"Dato: {datetime.now().strftime('%d. %B %Y')}\n\n"
        f"{len(articles)} forskningsartikler om tekstil, hud eller kvalitet, hentet fra "
        f"Europe PMC og OpenAlex og rangert lokalt. Skriv en omtale av hver av dem:\n\n"
        + "\n".join(lines)
    )


def _stream_writeups(client, articles: list[dict]) -> tuple[str, str | None]:
    collected, stop_reason = "", None
    try:
        with client.messages.stream(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=_writeup_system_prompt(),
            messages=[{"role": "user", "content": _build_user_content(articles)}],
        ) as stream:
            for chunk in stream.text_stream:
                print(chunk, end="", flush=True)
                collected += chunk
            stop_reason = stream.get_final_message().stop_reason
    except Exception as exc:
        print(f"\n⚠  Claude-kall feilet: {exc}")
    print()
    return collected, stop_reason


def _batch_refuses(client, articles: list[dict]) -> bool:
    """Billig probe med samme input-form som det ekte kallet. En transient feil under
    proben tolkes konservativt som «ikke refusal»."""
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=CLAUDE_PROBE_MAX_TOKENS,
            # Proben er et rent ja/nei på refusal — tenking ville bare spist
            # opp de 16 tokenene (lovlig å slå av: effort er «high» som standard).
            thinking={"type": "disabled"},
            system=_writeup_system_prompt(),
            messages=[{"role": "user", "content": _build_user_content(articles)}],
        )
    except Exception as exc:
        print(f"  ⚠  Refusal-probe feilet ({exc}) — antar ikke-refusal.")
        return False
    return resp.stop_reason == "refusal"


def _find_refusing_articles(client, articles: list[dict]) -> list[dict]:
    """Halvér fram til abstractet som trigger refusal. Avvises en halvdel kun som
    KOMBINASJON, fjernes den minste halvdelen for å bryte kombinasjonen."""
    def bisect(subset: list[dict]) -> list[dict]:
        if not _batch_refuses(client, subset):
            return []
        if len(subset) == 1:
            return list(subset)
        mid = len(subset) // 2
        left, right = subset[:mid], subset[mid:]
        bad = bisect(left) + bisect(right)
        return bad or list(left if len(left) <= len(right) else right)
    return bisect(articles)


_SKIP_RE = re.compile(r"^##\s*SKIP\b\s*\[?(\d+)\]?", re.IGNORECASE)
_TOPICS_RE = re.compile(r"^\*\*Emner:\*\*\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _parse_topics_line(block: str) -> list[str]:
    """Trekk **Emner:**-linjen ut av en omtale og valider mot registeret.

    Ukjente slugger forkastes stille — Claude finner av og til på et emnenavn, og et emne
    som ikke finnes ville blitt en død lenke. Blir det ingenting igjen, faller innrullingen
    tilbake på den lokale tildelingen."""
    m = _TOPICS_RE.search(block)
    if not m:
        return []
    raw = re.split(r"[,;]", m.group(1))
    out = []
    for part in raw:
        slug = part.strip().strip("`*_ ").lower()
        if slug in TOPICS_BY_SLUG and slug not in out:
            out.append(slug)
    return out[:3]


def _parse_writeups(text: str, articles: list[dict]) -> tuple[dict[str, str], set[str]]:
    """Del Claudes svar i én blokk per artikkel, mappet på URL — ikke på rekkefølge.
    En feilmapping ville gitt feil lenke under riktig tittel, så en blokk vi ikke kan
    knytte til en artikkel forkastes stille."""
    blocks: dict[str, str] = {}
    skipped: set[str] = set()
    by_url = {a["url"]: a for a in articles if a.get("url")}

    for raw in re.split(r"\n(?=##\s)", text.strip()):
        block = raw.strip().strip("-").strip()
        if not block.startswith("##"):
            continue
        m = _SKIP_RE.match(block)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(articles):
                skipped.add(articles[idx]["url"])
            continue
        for url in by_url:
            if url and url in block:
                blocks[url] = block
                break
    return blocks, skipped


def write_up_batch(queue: list[dict], batch_size: int) -> list[str]:
    """Få Claude til å skrive omtaler av de høyest scorede artiklene som mangler tekst,
    og lagre dem I KØEN. Returnerer refused-id-er (kalleren persisterer dem).

    Alt som skrives her, lagres — en artikkel koster tokens nøyaktig én gang, noensinne."""
    batch = [e for e in queue if e.get("status") == "scored"][:batch_size]
    if not batch:
        print("  ⚠  Ingen artikler i kø å skrive om.")
        return []

    client = anthropic.Anthropic()
    pool = list(batch)
    refused_ids: list[str] = []

    print(f"\nSkriver omtaler av {len(pool)} artikler med Claude (streamer svar)...\n")
    print("─" * 70)

    transient, refusal_rounds = 0, 0
    text = ""
    while True:
        collected, stop_reason = _stream_writeups(client, pool)
        if collected.strip():
            text = collected
            break
        if stop_reason == "refusal":
            refusal_rounds += 1
            if refusal_rounds > CLAUDE_REFUSAL_MAX_ROUNDS:
                print("✗  For mange avvisningsrunder — gir opp omtalene.")
                break
            print("\n⚠  Claude avviste batchen (sikkerhetsklassifikator). Isolerer...")
            bad = _find_refusing_articles(client, pool)
            if not bad:
                print("✗  Fant ingen enkeltartikkel å fjerne — gir opp.")
                break
            drop = {id(a) for a in bad}
            for a in bad:
                print(f"    – fjernet: {a['title'][:90]}")
                refused_ids.append(a["id"])
            pool = [a for a in pool if id(a) not in drop]
            if not pool:
                print("✗  Ingen artikler igjen etter filtrering — gir opp.")
                break
            print(f"  {len(pool)} artikler igjen — prøver på nytt.")
            print("─" * 70)
            continue
        transient += 1
        if transient >= CLAUDE_MAX_ATTEMPTS:
            break
        print(f"⚠  Tomt svar — nytt forsøk om {CLAUDE_RETRY_DELAY} s "
              f"({transient}/{CLAUDE_MAX_ATTEMPTS})...")
        time.sleep(CLAUDE_RETRY_DELAY)

    print("─" * 70)

    refused = set(refused_ids)
    for e in batch:
        if e["id"] in refused:
            e["status"] = "rejected"
            e["reject_reason"] = "refusal"
            e.pop("abstract", None)

    if not text.strip():
        print("✗  Tomt svar fra Claude — ingen omtaler lagret.")
        return refused_ids

    blocks, skipped = _parse_writeups(text, batch)
    if not blocks and not skipped:
        # Å lagre noe her ville knyttet feil tekst til feil artikkel.
        print("✗  Klarte ikke å knytte svaret til noen artikkel — ingenting lagret.")
        return refused_ids

    today = datetime.now().date().isoformat()
    wrote = vraket = 0
    for e in batch:
        if e.get("status") == "rejected":
            continue
        url = e["url"]
        if url in blocks:
            e["status"] = "ready"
            e["writeup"] = blocks[url]
            e["topics"] = _parse_topics_line(blocks[url]) or e.get("topics_primary") or []
            e["writeup_at"] = today
            e.pop("abstract", None)   # teksten er skrevet — abstractet trengs ikke mer
            wrote += 1
        elif url in skipped:
            e["status"] = "rejected"
            e["reject_reason"] = "vraket av Claude"
            e.pop("abstract", None)
            vraket += 1
        else:
            # Stille oversett. Én gang kan være et lengdekutt; gjentatt er den ubrukelig.
            e["passes"] = e.get("passes", 0) + 1
            if e["passes"] >= WRITEUP_MAX_PASSES:
                e["status"] = "rejected"
                e["reject_reason"] = f"hoppet over {e['passes']} ganger"
                e.pop("abstract", None)

    print(f"  ✓  {wrote} omtaler lagret i køen"
          + (f", {vraket} vraket av Claude" if vraket else ""))
    return refused_ids


# ─────────────────────────────────────────────────────────────────────────────
# Innrulling i kunnskapsbasen — aldri et API-kall
# ─────────────────────────────────────────────────────────────────────────────

def roll_into_kb(kb: dict, queue: list[dict], limit: int) -> list[dict]:
    """Flytt de høyest scorede ferdigskrevne artiklene fra køen inn i kunnskapsbasen.

    Studien lagres ÉN gang i `kb["studies"]`; emnene refererer til den med id. Det er
    forskjellen fra å kopiere omtalen inn under hvert emne: en studie som treffer tre
    emner finnes fortsatt bare ett sted, så en senere retting slår gjennom overalt."""
    ready = [e for e in queue if e.get("status") == "ready" and e.get("writeup")][:limit]
    today = datetime.now().date().isoformat()

    for e in ready:
        topics = [s for s in (e.get("topics") or []) if s in TOPICS_BY_SLUG]
        if not topics:
            # Claude ga ingen brukbar emnelinje og den lokale tildelingen var tom.
            # Da hører studien ikke hjemme noe sted, og vi lar den ligge i køen som
            # vraket i stedet for å opprette et hjemløst oppslag.
            e["status"] = "rejected"
            e["reject_reason"] = "ingen gyldige emner"
            continue

        kb["studies"][e["id"]] = {
            "id": e["id"], "doi": e.get("doi", ""), "url": e["url"], "title": e["title"],
            "journal": e.get("journal", "—"), "date": e.get("date", "—"),
            "source": e.get("source", ""), "category": e.get("category", ""),
            "writeup": e["writeup"], "topics": topics,
            "score": e.get("score", 0), "added": today,
        }
        for slug in topics:
            topic = kb["topics"].get(slug)
            if not topic:
                continue
            if e["id"] not in topic["studies"]:
                topic["studies"].insert(0, e["id"])
            topic["pending"] = topic.get("pending", 0) + 1
            topic["updated"] = today

    return ready


# ─────────────────────────────────────────────────────────────────────────────
# Emnesyntese — Claude skriver om ett oppslag av gangen
#
# Kostnaden her vokser med kunnskapsbasen, ikke med tilsiget, så to bremser: et emne
# syntetiseres først når det har fått SYNTH_PENDING_MIN ny evidens (teksten skal faktisk
# kunne bli en annen), og maks MAX_SYNTH_PER_RUN emner per kjøring. Inputen kappes til de
# nyeste SYNTH_MAX_STUDIES omtalene — et emne med 40 studier under seg skal ikke koste
# 40 omtaler i input hver gang det oppdateres.
# ─────────────────────────────────────────────────────────────────────────────

SYNTH_MAX_STUDIES = 14


def topics_due_for_synthesis(kb: dict) -> list[dict]:
    """Emner som står for tur, viktigste først: de som aldri har fått tekst går foran
    (et tomt oppslag er verre enn et litt utdatert), deretter mest ny evidens."""
    due = []
    for topic in kb["topics"].values():
        if topic.get("retired") or not topic["studies"]:
            continue
        pending = topic.get("pending", 0)
        first = not (topic.get("summary") or "").strip()
        if pending >= (SYNTH_FIRST_MIN if first else SYNTH_PENDING_MIN):
            due.append((0 if first else 1, -pending, topic))
    due.sort(key=lambda x: (x[0], x[1]))
    return [t for _, _, t in due]


def _synth_user_content(kb: dict, topic: dict) -> str:
    ids = topic["studies"][:SYNTH_MAX_STUDIES]
    blocks = []
    for sid in ids:
        s = kb["studies"].get(sid)
        if s:
            blocks.append(f"{s['writeup']}\n(Kilde: {s['journal']}, {s['date']})")
    prev = (topic.get("summary") or "").strip()
    parts = [
        f"EMNE: {topic['name']}  (slug: {topic['slug']}, gruppe: "
        f"{KIND_LABELS.get(topic['kind'], topic['kind'])})",
        f"BESKRIVELSE: {topic['blurb']}",
        f"ANTALL STUDIER UNDER EMNET: {len(topic['studies'])}"
        + (f" (de {len(ids)} nyeste vises under)" if len(topic["studies"]) > len(ids) else ""),
    ]
    if prev:
        parts.append(f"\nFORRIGE SAMMENDRAG (skal erstattes, ikke bygges videre på ordrett):\n{prev}")
    parts.append("\nEVIDENS:\n\n" + "\n\n---\n\n".join(blocks))
    return "\n".join(parts)


def _parse_synth(text: str) -> dict | None:
    """Plukk JSON-objektet ut av svaret. Claude legger av og til på en kodeblokk eller en
    innledende setning, så vi tar første balanserte {...} og validerer feltene mot
    registeret. Kan svaret ikke tolkes, returneres None og emnet står uendret — et halvt
    oppdatert oppslag er verre enn et uendret."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth, end = 0, -1
    in_str, esc = False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        return None
    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not (data.get("summary") or "").strip():
        return None

    criteria = []
    for c in data.get("criteria") or []:
        if not isinstance(c, dict) or not (c.get("text") or "").strip():
            continue
        criteria.append({
            "level": c.get("level") if c.get("level") in ("unngaa", "dokumenter", "foretrekk")
                     else "dokumenter",
            "text": c["text"].strip(),
            "strength": c.get("strength") if c.get("strength") in STRENGTHS else "moderat",
        })
    questions = [q.strip() for q in (data.get("questions") or [])
                 if isinstance(q, str) and q.strip()][:3]

    return {
        "summary": data["summary"].strip(),
        "verdict": data.get("verdict") if data.get("verdict") in VERDICTS else "ukjent",
        "confidence": data.get("confidence") if data.get("confidence") in STRENGTHS else "svak",
        "criteria": criteria[:4],
        "questions": questions,
    }


def synthesize_topic(client, kb: dict, topic: dict) -> bool:
    """Skriv ett emneoppslag på nytt. Returnerer True hvis KB-en ble oppdatert."""
    print(f"\n  ▸ syntetiserer «{topic['name']}» "
          f"({len(topic['studies'])} studier, {topic.get('pending', 0)} nye)...", flush=True)
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=SYNTH_MAX_TOKENS,
            system=SYNTH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _synth_user_content(kb, topic)}],
        )
    except Exception as exc:
        print(f"    ✗ Claude-kall feilet: {exc}")
        return False
    if resp.stop_reason == "refusal":
        print("    ✗ avvist av sikkerhetsklassifikatoren — emnet står uendret.")
        return False

    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    parsed = _parse_synth(text)
    if not parsed:
        print("    ✗ kunne ikke tolke svaret som JSON — emnet står uendret.")
        return False

    topic.update(parsed)
    topic["pending"] = 0
    topic["synth_at"] = datetime.now().date().isoformat()
    topic["updated"] = topic["synth_at"]
    print(f"    ✓ {VERDICTS[topic['verdict']].lower()}, {len(topic['criteria'])} krav, "
          f"{len(topic['questions'])} leverandørspørsmål")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Hovedprogram
# ─────────────────────────────────────────────────────────────────────────────

def _print_kb_state(kb: dict) -> None:
    topics = [t for t in kb["topics"].values() if not t.get("retired")]
    with_text = sum(1 for t in topics if (t.get("summary") or "").strip())
    criteria = sum(len(t.get("criteria") or []) for t in topics)
    print(f"Kunnskapsbase: {len(kb['studies'])} studier, "
          f"{with_text}/{len(topics)} emner skrevet, {criteria} innkjøpskrav.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tekstil-kunnskapsbase med Claude")
    parser.add_argument("--dry-run", action="store_true",
                        help="Hent, score og fyll køen uten å kalle Claude. Gratis, og "
                             "eneste trygge måte å teste spørringene på.")
    parser.add_argument("--seed", action="store_true",
                        help="Engangs oppstart: skriv mange omtaler og syntetiser mange "
                             "emner i én kjøring. Koster vesentlig mer enn en vanlig dag.")
    parser.add_argument("--writeups", type=int, default=None,
                        help="Overstyr antall omtaler Claude skriver denne kjøringen.")
    parser.add_argument("--synth", type=int, default=None,
                        help="Overstyr antall emner som syntetiseres denne kjøringen.")
    args = parser.parse_args()

    _load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY") and not args.dry_run:
        print("Feil: ANTHROPIC_API_KEY er ikke satt.")
        sys.exit(1)

    today = datetime.now().date()
    print(f"\n{'─'*70}")
    print(f"  Tekstil-kunnskapsbase  —  {datetime.now().strftime('%A %d. %B %Y')}")
    print(f"{'─'*70}\n")

    # Hvor mye som gjøres denne kjøringen. --seed er engangsknappen som gjør basen
    # brukbar fra dag én i stedet for om tre måneder.
    writeup_rounds = 1
    n_writeups = args.writeups if args.writeups is not None else WRITEUP_BATCH_SIZE
    n_rollin = MAX_ROLLIN_PER_RUN
    n_synth = args.synth if args.synth is not None else MAX_SYNTH_PER_RUN
    if args.seed:
        writeup_rounds = 6          # 6 × 8 = opptil 48 omtaler
        n_rollin = 60
        n_synth = args.synth if args.synth is not None else 20

    # ── 1–2. Last og prun ────────────────────────────────────────────────────
    kb = _load_kb()
    seen = _load_seen()
    queue = _prune_queue(_load_queue(), seen, today)
    counts = _queue_counts(queue)
    _print_kb_state(kb)
    print(f"Kø: {counts['ready']} ferdigskrevne, {counts['scored']} venter på tekst, "
          f"{counts['rejected']} vraket.")

    # ── 3. Påfyll ────────────────────────────────────────────────────────────
    need = QUEUE_REFILL_BELOW * (writeup_rounds if args.seed else 1)
    if counts["scored"] >= need:
        print(f"  ⓘ  {counts['scored']} artikler i kø (≥ {need}) — hopper over henting.")
    else:
        print(f"\nHenter fra Europe PMC og OpenAlex, siste {LOOKBACK_DAYS} dager...")
        refill_queue(queue, seen, today)
        _save_queue(queue)
        counts = _queue_counts(queue)
        print(f"  → kø: {counts['ready']} ferdige, {counts['scored']} venter på tekst")

    if args.dry_run:
        print("\n  ⓘ  --dry-run: hopper over Claude, ruller ikke inn, syntetiserer ikke.")
        top = sorted(queue, key=lambda e: -e.get("score", 0))[:15]
        print(f"\nTopp {len(top)} i køen:")
        for e in top:
            print(f"  {e.get('score', 0):5.1f}  [{','.join(e.get('topics_primary') or []) or '—'}]"
                  f"  {e['title'][:70]}")
        sys.exit(0)

    # ── 4. Claude skriver omtaler ────────────────────────────────────────────
    refused_ids: list[str] = []
    for round_i in range(writeup_rounds):
        counts = _queue_counts(queue)
        if counts["ready"] >= WRITEUP_REFILL_BELOW and not args.seed:
            print(f"\n  ⓘ  {counts['ready']} ferdigskrevne i kø (≥ {WRITEUP_REFILL_BELOW}) "
                  "— hopper over Claude-kallet. Gratis dag.")
            break
        if counts["scored"] == 0:
            print("\n  ⚠  Ingen artikler i kø å skrive om.")
            break
        if writeup_rounds > 1:
            print(f"\n── omtalerunde {round_i + 1}/{writeup_rounds} ──")
        refused_ids += write_up_batch(queue, n_writeups)
        _save_queue(queue)

    # Refused lagres UANSETT hvordan resten gikk — uten flagget kommer nøyaktig samme
    # abstract tilbake i morgen og betaler hele isoler-og-fjern-runden på nytt.
    if refused_ids:
        _save_seen(seen, [], refused_ids)
        print(f"  ⓘ  {len(refused_ids)} avvist(e) abstract(s) merket refused.")

    # ── 5. Innrulling ────────────────────────────────────────────────────────
    rolled = roll_into_kb(kb, queue, n_rollin)
    ok = [e for e in rolled if e.get("status") != "rejected"]
    if ok:
        print(f"\nRullet inn {len(ok)} studier i kunnskapsbasen:")
        for e in ok:
            print(f"    {e.get('score', 0):5.1f}  [{','.join(e['topics'])}]  {e['title'][:62]}")
        _save_seen(seen, [e["id"] for e in ok], [])
        done = {e["id"] for e in ok}
        queue = [e for e in queue if e["id"] not in done]
    else:
        print("\n  ⚠  Ingen ferdigskrevne studier å rulle inn.")
    _save_queue(queue)
    _save_kb(kb)

    # ── 6. Syntese ───────────────────────────────────────────────────────────
    due = topics_due_for_synthesis(kb)
    if not due:
        print("\n  ⓘ  Ingen emner har nok ny evidens til å skrives om. Gratis syntese.")
    else:
        print(f"\n{len(due)} emner står for tur — syntetiserer {min(n_synth, len(due))}:")
        client = anthropic.Anthropic()
        for topic in due[:n_synth]:
            if synthesize_topic(client, kb, topic):
                _save_kb(kb)   # lagre etter hvert emne: en feil senere skal ikke koste de før

    _save_kb(kb)
    print()
    _print_kb_state(kb)
    left = _queue_counts(queue)
    print(f"Kø: {left['ready']} ferdigskrevne igjen, {left['scored']} venter på tekst.")
    still_due = len(topics_due_for_synthesis(kb))
    if still_due:
        print(f"  ⓘ  {still_due} emner venter fortsatt på syntese "
              f"(~{-(-still_due // max(n_synth, 1))} kjøringer).")


if __name__ == "__main__":
    main()
