#!/usr/bin/env python3
"""
supplement_briefing.py  —  Kunnskapsbase om kosttilskudd

Bygger opp forskning.modr.no/tilskudd: et voksende oppslagsverk der hvert stoff får én
dom, dosen som faktisk er brukt i studiene, hvem det gjelder for, og — viktigst — hva
stoffet PÅSTÅS å gjøre satt opp mot hva studiene faktisk har MÅLT.

Kjør:
    python supplement_briefing.py            # full kjøring (bruker Claude-kvote)
    python supplement_briefing.py --dry-run  # hent, score og køfyll uten Claude — gratis
    python supplement_briefing.py --seed     # engangs: fyll basen fra dag én

Systemet er KØBASERT og AKKUMULERENDE, som `textile_briefing.py` og til forskjell fra
`research_briefing.py`. En kjøring er syv steg, der de tunge hoppes over når de ikke
trengs:

    1. Last kunnskapsbase, kø og dedup-cache.
    2. Prun køen.
    3. Påfyll fra Europe PMC        — kun når køen er kort (QUEUE_REFILL_BELOW).
    4. Mål bevis-gulvet             — kun for stoffer uten evidens. Gratis, ingen Claude.
    5. Claude skriver omtaler       — kun når lageret av ferdige tekster er lavt.
    6. Rull inn i kunnskapsbasen    — ren datamanipulasjon, aldri et API-kall.
    7. Syntetiser stoffer med ny evidens — Claude, maks MAX_SYNTH_PER_RUN per kjøring.

TO TING SKILLER DENNE FRA TEKSTIL-GENERATOREN, OG BEGGE ER BEVISSTE:

1. RCT-KRAVET GJELDER HER. Tekstilallergi må dokumenteres med patch-tester og kohorter
   fordi randomiserte forsøk på plagg knapt finnes; tilskudd er det motsatte — feltet er
   fullt av RCT-er og metaanalyser, og feltet er samtidig fullt av små, sponsede,
   positive studier. Kvalitetsfilteret er derfor det samme som i research_briefing.py
   (KW:"Humans" + RCT/metaanalyse/systematisk oversikt + SRC:MED). Målt 22. august 2026
   gir det 2 448 kvalifiserende studier på 730 dager fordelt på registerets stoffer —
   rikelig. Å senke kravet ville fylt basen med nøyaktig det materialet som driver hypen.

2. BEVIS-GULVET ER EN FUNKSJON, IKKE EN MANGEL. Stoffene i gruppen `uavklart` gir null
   treff gjennom filteret over — BPC-157 har 193 publikasjoner og 0 kontrollerte
   menneskestudier, de øvrige peptidene 395 og 0. Fraværet av evidens ER svaret for
   nettopp de stoffene som markedsføres hardest, så `measure_evidence_floor()` teller
   begge tallene og lagrer dem på stoffet. Et stoff uten oppslag ville latt leseren tro
   at spørsmålet ikke er stilt.

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
from supplement_topics import (
    TOPICS, TOPICS_BY_SLUG, KIND_LABELS, KIND_ORDER, VERDICTS, VERDICT_ORDER, STRENGTHS,
)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

MODEL = "claude-opus-5"                # samme modell som de tre andre generatorene
MAX_TOKENS = 32000                   # 8 omtaler + Opus 5 sin tenking
SYNTH_MAX_TOKENS = 12000             # ett stoffoppslag + tenking

# Tilskuddsforskning har ingen nyhetssyklus, men den har en resepsjonssyklus: en
# metaanalyse fra 2019 er ofte overhalt av en større fra 2025. To år holder oppslaget
# forankret i det som er gjeldende uten å kaste bort de store metaanalysene.
LOOKBACK_DAYS = 730

PAGE_SIZE = 100
# Per STOFF, ikke per gruppe. Vi spør Europe PMC én gang per stoff (bygget av `probe` i
# registeret), så et stoff aldri kan bli utkonkurrert av et større nabostoff i samme
# spørring. Største enkeltstoff er D-vitamin med 417 treff på to år; taket kutter kun
# der, og da de eldste.
MAX_FETCH_PER_TOPIC = 300

# ── Filene ───────────────────────────────────────────────────────────────────
QUEUE_FILE = "supplement_queue.json"
KB_FILE = "supplement_kb.json"
SEEN_FILE = "supplement_seen.json"

QUEUE_REFILL_BELOW = 40    # under så mange «scored» i kø: hent nytt fra Europe PMC
# Tak på antall studier som venter på tekst. Første påfyll ga 1 616 kandidater over
# terskel — vi skriver 8 omtaler per kjøring, så halen av den køen ville aldri blitt
# nådd, mens abstractene i den ligger og gjør køfila unødig stor (hver oppføring bærer
# et fullt abstract fram til omtalen er skrevet). Kuttet tar de LAVEST scorede, og de
# er ikke tapt: de står ikke i `seen`, så de scores på nytt ved neste påfyll.
# «ready» og «rejected» røres aldri — de er betalt for, respektive gravsteiner.
QUEUE_MAX_SCORED = 400
QUEUE_MAX_AGE_DAYS = 1100  # prun køoppføringer eldre enn dette (publiseringsdato)
SEEN_RETENTION_DAYS = 1500 # en innrullet studie skal aldri hentes inn igjen

WRITEUP_REFILL_BELOW = 8   # under så mange «ready» i kø: kall Claude
WRITEUP_BATCH_SIZE = 8     # omtaler per Claude-kall (MAX_TOKENS må følge med)
WRITEUP_MAX_PASSES = 2     # hoppet stille over så mange ganger → rejected

MAX_ROLLIN_PER_RUN = 5     # studier som rulles inn i KB-en per kjøring
MAX_SYNTH_PER_RUN = 3      # stoffer som re-syntetiseres per kjøring

SYNTH_PENDING_MIN = 2      # antall nye studier før et stoff står for tur
SYNTH_FIRST_MIN = 1        # …men et stoff uten oppslag i det hele tatt trenger bare én
SYNTH_MAX_STUDIES = 14     # nyeste omtaler som sendes inn ved syntese

MAX_ABSTRACT_CHARS = 4000  # maks tegn fra hvert abstract i PROMPTEN (aldri før scoring)

# Bevis-gulvet måles på nytt først når det er så gammelt at et nytt forsøk kan ha rukket
# å bli indeksert. To gratis HTTP-kall per stoff, men bare for stoffer uten evidens.
FLOOR_RECHECK_DAYS = 30

CLAUDE_MAX_ATTEMPTS = 3
CLAUDE_RETRY_DELAY = 5
CLAUDE_REFUSAL_MAX_ROUNDS = 4
CLAUDE_PROBE_MAX_TOKENS = 16

MIN_SCORE = 3.0            # under dette settes en studie aldri i kø

_UA = "tilskudd-briefing/1.0 (https://forskning.modr.no; personal research project)"
_PMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_HEADERS = {"User-Agent": _UA}

# Kvalitetsfilteret. Identisk med research_briefing.py — se CLAUDE.md for hvorfor
# `KW:"Humans"` og ALDRI `MESH:"Humans"` (feltet er dødt i Europe PMC og fjernet 92 % av
# materialet da det sto der).
_PMC_SUFFIX = (
    ' AND KW:"Humans"'
    ' AND (PUB_TYPE:"Randomized Controlled Trial" OR PUB_TYPE:"Meta-Analysis"'
    ' OR PUB_TYPE:"Systematic Review")'
    " AND SRC:MED AND LANG:eng AND HAS_ABSTRACT:Y"
)


def topic_query(topic: dict, from_date: str, today: date) -> str:
    """Spørringen for ett stoff. Bygges av `probe` i registeret, så registeret er både
    emneliste OG søkespesifikasjon — et nytt stoff blir søkt opp uten kodeendring."""
    dates = f" AND (FIRST_PDATE:[{from_date} TO {today.isoformat()}])"
    return topic["probe"] + _PMC_SUFFIX + dates


# ─────────────────────────────────────────────────────────────────────────────
# Prompter
# ─────────────────────────────────────────────────────────────────────────────

def _topic_menu() -> str:
    """Stofflista slik Claude ser den. Bygges fra registeret."""
    lines = []
    for kind in KIND_ORDER:
        lines.append(f"\n{KIND_LABELS[kind].upper()}:")
        for t in TOPICS:
            if t["kind"] == kind:
                lines.append(f"  {t['slug']} — {t['name']}: {t['blurb']}")
    return "\n".join(lines)


WRITEUP_SYSTEM_PROMPT = """Du bygger et norsk oppslagsverk om kosttilskudd. Leseren er en oppegående lekperson opptatt av longevity, som vil vite hva han faktisk bør ta og hva han trygt kan la være. Han er analytisk og vil ha tall.

FORMÅLET MED HELE OPPSLAGSVERKET, som skal styre alt du skriver: det skal gjøre leseren i stand til å si NEI raskt. De fleste tilskudd gjør ingenting målbart for en frisk person. Verdien ligger i å slippe å prøve dem — og i å vite hvilke få som faktisk har tall bak seg, i hvilken dose, for hvem. Tilskuddslitteraturen er systematisk skjevfordelt mot positive funn, med små utvalg og industrifinansiering. Skriv med det i bakhodet: et nøkternt referat av en svak studie er riktig, en entusiastisk gjenfortelling er feil.

Du får en liste med studier (stoff, tittel, tidsskrift, dato, URL, engelsk sammendrag). Alle er allerede menneskestudier av typen RCT, metaanalyse eller systematisk oversikt, og alle er forhåndsrangert av en lokal scoring. Du skal ikke velge mellom dem — du skal skrive om hver av dem.

VRAKING: er en studie likevel ubrukelig, skal du IKKE skrive en omtale. Skriv i stedet nøyaktig denne linjen, alene på en linje:
## SKIP [n] — kort begrunnelse
der [n] er studiens nummer i listen. Vrak kun når ett av disse er oppfylt:
1. Den handler ikke egentlig om et kosttilskudd — stoffet er en biomarkør, en laboratoriemåling eller en bisetning, ikke noe noen tar.
2. Den gjelder utelukkende en pasientgruppe eller livssituasjon leseren ikke er i (graviditet, spedbarn, dialyse, kreftbehandling, intensivavdeling), uten overføringsverdi.
3. Den har ingen konkrete tall — bare «signifikant bedring» uten effektstørrelser.
Vraking skal være unntaket. Er du i tvil, skriv omtalen.

STOFFTILDELING: hver omtale skal knyttes til 1–2 stoffer fra listen under, med eksakte slug-navn. Velg det studien FAKTISK undersøkte som intervensjon — ikke stoffer den bare nevner i diskusjonen.

TILGJENGELIGE STOFFER:{TOPIC_MENU}

FORMAT — for hver studie du skriver om, nøyaktig denne strukturen:
## [Norsk tittel som bærer hovedfunnet](URL)
**Stoffer:** slug1, slug2
**Metode:** Hva slags studie, hvor mange deltakere, hvem var de (alder, helsetilstand, trent/utrent), hvor lenge varte den, og hva ble den sammenlignet mot? Oppgi DOSE og FORM eksplisitt når det finnes — det er det leseren trenger for å vite hva et funn faktisk gjelder. 3–4 setninger.
**Resultat:** Hovedtallene. Effektstørrelser med konfidensintervall, absolutte endringer og prosent — og si alltid hva som er UTFALLET som ble målt. Skill tydelig mellom et hardt utfall (dødelighet, brudd, hjerteinfarkt, kilo, sekunder) og et surrogat (en blodverdi, en biomarkør, et spørreskjema). Er utfallet et surrogat, skal det stå. 3–4 setninger.
**Hva det betyr for deg:** Hva denne ene studien endrer for en frisk voksen som vurderer stoffet. Er svaret «ingenting — dette gjaldt en annen gruppe» eller «effekten er reell, men så liten at den ikke er verdt kapselen», så skriv det. 2–3 setninger.
**Forbehold:** Hva studien IKKE viser. Finansiering fra produsent; lite utvalg; kort varighet; utfall som er en blodverdi og ikke en helsegevinst; deltakere med mangel der funnet ikke overføres til noen med normal status; metaanalyse av små, skjeve primærstudier. 1–2 setninger.

REGLER:
- Tittelen skal si HVA studien fant, med retning og tall. Godt: «Kreatin ga 1,4 kg mer muskelmasse over 12 uker». Dårlig: «Studie om kreatin og trening».
- Bruk ALLTID den oppgitte URL-en i lenken, uendret.
- Oppgi alltid dose med enhet (mg, µg, IE, g) når sammendraget har den.
- Ikke overdriv funn utover det sammendraget støtter, og ikke dikt opp tall.
- Norsk. Forklar forkortelser første gang. Ingen fyllord, ingen innledning — start rett på første ## studie."""


SYNTH_SYSTEM_PROMPT = """Du vedlikeholder ett oppslag i et norsk oppslagsverk om kosttilskudd. Leseren er en oppegående lekperson opptatt av longevity, som bruker oppslaget til å bestemme om han skal ta stoffet eller ikke.

FORMÅLET: oppslaget skal gjøre leseren i stand til å si NEI raskt. De fleste tilskudd gjør ingenting målbart for en frisk person. Et ærlig «dette er godt undersøkt, og det virker ikke» er et av de mest verdifulle svarene du kan gi — og det er et ANNET svar enn «vi vet ikke». Bland dem aldri.

Du får stoffets navn, hva det MARKEDSFØRES SOM, en kort beskrivelse, eventuelt forrige oppslag, og all evidensen som ligger under stoffet (omtaler skrevet tidligere). Skriv oppslaget på nytt i sin helhet, basert på ALL evidensen — ikke bare det nye.

Svar med ETT JSON-objekt og ingenting annet. Ingen kodeblokk, ingen forklaring rundt:

{
  "summary": "<markdown, 2–4 avsnitt>",
  "measured": "<hva studiene FAKTISK har målt, satt opp mot påstanden>",
  "verdict": "<ta | vurder | dropp | risiko | ukjent>",
  "confidence": "<sterk | moderat | svak>",
  "who": "<hvem dette gjelder for, og hvem det ikke gjelder for>",
  "dose": [
    {"text": "<dose og form brukt i studiene, med enhet>", "strength": "<sterk | moderat | svak>"}
  ],
  "interactions": ["<interaksjon, øvre grense eller bivirkning verdt å kjenne>"],
  "changes_verdict": ["<hva som skal til for at dommen endres>"]
}

FELTENE:
- `summary`: Selve oppslaget. Første avsnitt sier hva saken er og hvor evidensen står i dag. Deretter hva som er målt, med tall. Til slutt hva som er uavklart. **Fet** på nøkkeltall. Ikke gjenta omtalene ordrett — syntetiser, og si det tydelig når studiene spriker eller når de gode studiene er negative og de positive er små. Maks 350 ord.
- `measured`: DET VIKTIGSTE FELTET. Sett markedsføringspåstanden opp mot utfallene som faktisk er målt, i én til tre setninger. Er påstanden «klarhet i hodet» mens studiene måler en blodkonsentrasjon, skal det stå rett ut. Er påstanden faktisk testet på det utfallet den lover, skal DET stå. Ikke vær retorisk — vær presis.
- `verdict`:
    ta      — konsistent effekt på et utfall som betyr noe, dose kjent, trygghet god
    vurder  — reell effekt, men liten, betinget eller kun i en undergruppe (f.eks. ved mangel)
    dropp   — godt undersøkt, og effekten er omtrent null. En STERK konklusjon, ikke en svak.
    risiko  — dokumentert skade, uavklart sikkerhet, eller uregulert produkt uten humane sikkerhetsdata
    ukjent  — for tynt grunnlag til å konkludere. Bruk denne når evidensen er det.
- `confidence`: hvor tungt evidensgrunnlaget veier SAMLET (antall studier, design, hvor entydige de er, hvor mye industrifinansiering).
- `who`: hvem funnene gjelder. Skill eksplisitt mellom personer med påvist mangel og personer med normal status — for de fleste vitaminer er det hele forskjellen.
- `dose`: 0–3 doseringer slik de faktisk er brukt i studiene, med enhet og form («5 g kreatin monohydrat daglig, ingen ladefase nødvendig»). IKKE dosen som står på boksen, og ikke en dose du gjetter. Har evidensen ingen brukbar dose, returner tom liste.
- `interactions`: 0–3 punkter om øvre grenser, kjente bivirkninger eller interaksjoner med legemidler eller andre tilskudd. Tom liste er gyldig.
- `changes_verdict`: 0–3 punkter om hvilken studie eller hvilket funn som ville endret dommen. Dette er hva leseren skal se etter framover.

REGLER:
- Bygg KUN på evidensen du får. Ikke hent inn påstander utenfra, og ikke dikt opp tall eller doser.
- Skill hardt mellom harde utfall og surrogatmål. En blodverdi som stiger er ikke en helsegevinst.
- Er evidensen tynn eller motstridende, SI det og sett `verdict` til `ukjent`. Er den god og negativ, sett `dropp` — ikke `ukjent`.
- Ikke gi medisinske råd til syke, og ikke foreslå at leseren slutter med legemidler.
- Norsk. Ingen fyllord."""


# ─────────────────────────────────────────────────────────────────────────────
# Lagring — tre filer i BRIEFING_DATA_DIR (MÅ persisteres, se CLAUDE.md)
#
#   supplement_kb.json     — kunnskapsbasen. Det eneste nettsiden leser. Kan IKKE
#                            regenereres: den er summen av hver omtale og hver syntese.
#   supplement_queue.json  — studier vurdert, men ennå ikke rullet inn.
#   supplement_seen.json   — {id: {"last": dato, "rolled": bool, "refused": bool}}
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
    """To varige grunner til å holde en studie ute: den er allerede rullet inn, eller den
    ble avvist av sikkerhetsklassifikatoren (deterministisk — nytt forsøk koster bare en
    ny bisect-runde)."""
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


def _trim_queue(queue: list[dict]) -> list[dict]:
    """Behold de QUEUE_MAX_SCORED høyest scorede som venter på tekst. `ready` (betalt
    for) og `rejected` (gravsteiner som hindrer reinnsetting) beholdes uansett."""
    scored = [e for e in queue if e.get("status", "scored") == "scored"]
    if len(scored) <= QUEUE_MAX_SCORED:
        return queue
    scored.sort(key=lambda e: e.get("score", 0.0), reverse=True)
    keep = {id(e) for e in scored[:QUEUE_MAX_SCORED]}
    return [e for e in queue
            if e.get("status", "scored") != "scored" or id(e) in keep]


def _save_queue(queue: list[dict]) -> None:
    queue[:] = _trim_queue(queue)
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
    """Les KB-en og synkroniser den mot stoffregisteret.

    Stoffer lagt til i supplement_topics.py siden sist opprettes tomme; stoffer som er
    FJERNET fra registeret blir stående med evidensen sin (vi sletter aldri noe som er
    betalt for), men markeres `retired` så nettsiden kan tone dem ned."""
    kb = _read_json(_data_path(KB_FILE), None) or {}
    topics = kb.get("topics") or {}
    studies = kb.get("studies") or {}

    for t in TOPICS:
        cur = topics.get(t["slug"]) or {}
        cur.update({
            "slug": t["slug"], "name": t["name"], "kind": t["kind"], "blurb": t["blurb"],
            "claim": t["claim"], "retired": False,
        })
        cur.setdefault("summary", "")
        cur.setdefault("measured", "")
        cur.setdefault("verdict", t.get("floor_verdict") or "ukjent")
        cur.setdefault("confidence", "svak")
        cur.setdefault("who", "")
        cur.setdefault("dose", [])
        cur.setdefault("interactions", [])
        cur.setdefault("changes_verdict", [])
        cur.setdefault("studies", [])       # studie-id-er, nyeste først
        cur.setdefault("pending", 0)        # ny evidens siden siste syntese
        cur.setdefault("evidence_floor", None)
        cur.setdefault("updated", None)
        cur.setdefault("synth_at", None)
        # `floor_verdict` er et faktum om markedet, ikke en tolkning av forskning, og
        # skal derfor slå gjennom fra registeret hver kjøring — helt til stoffet faktisk
        # får evidens og en syntese overtar dommen.
        if t.get("floor_verdict") and not cur["studies"]:
            cur["verdict"] = t["floor_verdict"]
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
# Stofftildeling — gratis og deterministisk, før Claude ser noe som helst
# ─────────────────────────────────────────────────────────────────────────────

def assign_topics(title: str, abstract: str) -> tuple[list[str], list[str]]:
    """Knytt en studie til stoffer ut fra søkeordene i registeret.

    (primære, sekundære): treff i TITTELEN er primært (studien undersøkte stoffet),
    treff kun i sammendraget sekundært (stoffet er nevnt). En studie uten et eneste
    primært stoff nevner nesten alltid tilskuddet i forbifarten, og straffes."""
    t, a = title.lower(), abstract.lower()
    primary, secondary = [], []
    for topic in TOPICS:
        if any(term in t for term in topic["terms"]):
            primary.append(topic["slug"])
        elif any(term in a for term in topic["terms"]):
            secondary.append(topic["slug"])
    return primary, secondary


# ─────────────────────────────────────────────────────────────────────────────
# Lokal scoring — gratis grovsortering før Claude
#
# Spørringene er per stoff og dermed presise, men presis er ikke det samme som
# relevant: D-vitamin gir 417 treff på to år, og flertallet av dem handler om
# graviditet, spedbarn, dialyse eller husdyr. Scoringen er der utvalget skjer.
# ─────────────────────────────────────────────────────────────────────────────

_STATS_PATTERNS = [
    r"\b95\s*%?\s*(ci|confidence)",
    r"\bci\b\s*[:=]?\s*[\[(]",
    r"\b(odds ratio|or)\s*[:=]?\s*\d",
    r"\b(risk ratio|relative risk|rr)\s*[:=]?\s*\d",
    r"\b(hazard ratio|hr)\s*[:=]?\s*\d",
    r"\b(smd|standardized mean difference|weighted mean difference|wmd)\b",
    r"\bp\s*[<=>]\s*0?\.\d",
    r"\b\d{1,3}(\.\d+)?\s*%",
    r"\bn\s*=\s*\d",
]

# Studiedesign (matches mot pubTypeList). Samme rangering som research_briefing.py.
_DESIGN_POINTS = [
    ("meta-analysis", 4.0),
    ("randomized controlled trial", 3.0),
    ("systematic review", 2.0),
]

# Dose og form. Et sammendrag som oppgir dosen er et sammendrag det går an å skrive
# «Metode» av — og dosen er det leseren faktisk trenger.
_DOSE_PATTERNS = [
    r"\b\d+(\.\d+)?\s*(mg|µg|ug|mcg|g|iu|i\.u\.)\b",
    r"\b\d+(\.\d+)?\s*(mg|g)\s*/\s*(day|d|kg)\b",
    r"\bdaily dose\b", r"\bdosage\b", r"\bdose-response\b",
]

# Harde utfall — det som faktisk betyr noe, i motsetning til en blodverdi som stiger.
_HARD_OUTCOMES = [
    "all-cause mortality", "mortality", "cardiovascular event", "myocardial infarction",
    "stroke", "fracture", "incidence", "hospitalization", "body composition",
    "lean mass", "muscle mass", "strength", "vo2max", "time to exhaustion",
    "sleep onset latency", "cognitive performance", "depression score", "anxiety score",
    "blood pressure", "hba1c", "ldl cholesterol", "insulin sensitivity", "grip strength",
]

# ── De to støytypene som MÅ straffes ────────────────────────────────────────
#
# 1. FEIL POPULASJON. Vitaminlitteraturen domineres av graviditet, spedbarn og
#    intensivmedisin. Det er god forskning som svarer på et annet spørsmål enn vårt:
#    leseren er en frisk voksen mann. Uten denne lista ville D-vitamin-oppslaget blitt
#    skrevet på svangerskapsstudier.
_OFF_POPULATION = [
    "pregnan", "maternal", "gestation", "lactating", "breastfeeding", "infant",
    "neonat", "preterm", "newborn", "children", "child ", "pediatric", "paediatric",
    "adolescent", "school-age", "toddler",
    "dialysis", "hemodialysis", "critically ill", "intensive care", "icu ",
    "mechanical ventilation", "sepsis", "covid-19", "postoperative", "perioperative",
    "surgery", "surgical", "transplant", "cancer", "chemotherap", "oncolog", "tumor",
    "tumour", "palliative", "hiv", "tuberculosis", "malaria", "cirrhosis",
    "schizophrenia", "psychiatric inpatient", "dementia patients", "parkinson",
    # Dyr og landbruk — slipper gjennom via oversiktsartikler tross KW:"Humans".
    "broiler", "poultry", "piglet", "swine", "dairy cow", "livestock", "aquaculture",
    "rats", "mice", "murine", "in vitro", "cell line",
]

# 2. IKKE ET TILSKUDD. Stoffet er en biomarkør, en analysemetode eller en
#    legemiddelbehandling — ikke noe leseren kan kjøpe og ta. «Serum zinc as a
#    prognostic marker in sepsis» treffer stoffet `sink` perfekt og er helt ubrukelig.
_NOT_A_SUPPLEMENT = [
    "biomarker", "prognostic marker", "diagnostic accuracy", "serum levels as",
    "assay", "chromatograph", "spectrometry", "quantification of", "determination of",
    "fortification of", "food fortification", "biofortif",
    "intravenous", "parenteral", "injection of", "infusion",
    "enteral nutrition", "total parenteral",
    "drug interaction with", "pharmacokinetic",
]

_N_PATTERNS = [
    r"\bn\s*=\s*([\d,\. ]{2,12})",
    r"([\d,\. ]{2,12})\s*(participants|patients|subjects|individuals|adults|men|women)",
    r"(?:including|involving|comprising|enrolled)\s+([\d,\. ]{2,12})\s",
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
    pubtypes = " ".join(article.get("pub_types") or []).lower()
    score, why = 0.0, []

    # Stofftreff er inngangsbilletten.
    primary = article.get("topics_primary") or []
    secondary = article.get("topics_secondary") or []
    if primary:
        score += min(2.5 + 0.5 * (len(primary) - 1), 3.5)
        why.append(f"{len(primary)} stoff")
    else:
        score -= 2.5
        why.append("−intet primærstoff")
    if secondary:
        score += min(0.3 * len(secondary), 1.0)

    for name, pts in _DESIGN_POINTS:
        if name in pubtypes:
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
        score -= 2.5   # ingen tall → ingenting å skrive «Resultat» av
        why.append("−ingen tall")

    if any(re.search(p, text) for p in _DOSE_PATTERNS):
        score += 1.2
        why.append("dose oppgitt")
    else:
        score -= 1.0   # uten dose kan omtalen ikke si hva funnet faktisk gjelder

    outcome_hits = sum(1 for t in _HARD_OUTCOMES if t in text)
    if outcome_hits:
        score += min(0.6 * outcome_hits, 2.5)

    # Feil populasjon: hardt på tittelen (da er DET studien handler om), mildt i
    # sammendraget (der kan det være en delanalyse eller en setning i diskusjonen).
    off_title = sum(1 for t in _OFF_POPULATION if t in title)
    if off_title:
        score -= 4.0 * off_title
        why.append(f"−feil populasjon×{off_title}")
    elif any(t in abstract for t in _OFF_POPULATION):
        score -= 1.0
        why.append("−klinisk kontekst")

    not_supp = sum(1 for t in _NOT_A_SUPPLEMENT if t in title)
    if not_supp:
        score -= 4.0 * not_supp
        why.append(f"−ikke tilskudd×{not_supp}")
    elif any(t in abstract for t in _NOT_A_SUPPLEMENT):
        score -= 0.8

    return score, ", ".join(why)


# ─────────────────────────────────────────────────────────────────────────────
# Henting fra Europe PMC
# ─────────────────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _fetch_pmc(query: str, cap: int) -> list[dict]:
    """Paginer gjennom Europe PMC via cursorMark. Myk feil: en feil på side 2+ kaster
    ikke bort sidene vi allerede har."""
    out, cursor = [], "*"
    while len(out) < cap:
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


def _hit_count(query: str, attempts: int = 3) -> int | None:
    """Antall treff uten å hente dem — grunnlaget for bevis-gulvet.

    Med retry og økende pause: bevis-gulvet måles rett etter 34 paginerte spørringer, og
    Europe PMC begynner da å avvise. Første testkjøring mistet 15 av 34 målinger på
    nøyaktig dette. Et tapt gulv er ikke katastrofalt (det måles på nytt neste kjøring),
    men for stoffene i `uavklart` ER gulvet oppslaget, så det er verdt tre forsøk."""
    for attempt in range(attempts):
        try:
            resp = httpx.get(_PMC_API, params={
                "query": query, "format": "json", "pageSize": "1", "resultType": "idlist",
            }, headers=_HEADERS, timeout=60)
            resp.raise_for_status()
            return int(resp.json().get("hitCount", 0))
        except Exception:
            if attempt == attempts - 1:
                return None
            time.sleep(2 * (attempt + 1))
    return None


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
        "category": category,
        "pub_types": list((r.get("pubTypeList") or {}).get("pubType") or []),
    }


def _insert_scored(queue: list[dict], article: dict, score: float, why: str) -> bool:
    """Sett en nyscoret studie inn i køen. False hvis id-en allerede står der.
    Sorteringen skjer i _save_queue(), så plassering etter score faller ut av seg selv."""
    if any(e["id"] == article["id"] for e in queue):
        return False
    queue.append({
        **{k: article[k] for k in
           ("id", "doi", "url", "title", "journal", "date", "category", "abstract")},
        "topics_primary": article.get("topics_primary") or [],
        "topics_secondary": article.get("topics_secondary") or [],
        "score": round(score, 2),
        "score_why": why,
        "queued_at": datetime.now().date().isoformat(),
        "status": "scored",
        "passes": 0,
        "writeup": None,
        "topics": None,        # settes av Claude ved omtale (**Stoffer:**)
        "writeup_at": None,
    })
    return True


def refill_queue(queue: list[dict], seen: dict, today: date) -> int:
    """Spør Europe PMC én gang per stoff, tildel stoffer lokalt, score, og sett alt over
    MIN_SCORE i kø.

    Én spørring PER STOFF, ikke per gruppe: slår man ni vitaminer sammen i én spørring og
    kutter på MAX_FETCH, er det alltid det største stoffet som overlever kuttet. K2 (9
    kvalifiserende studier på to år) ville aldri sett dagens lys ved siden av D-vitamin
    (417). Kostnaden er 34 HTTP-kall, og de er gratis."""
    from_date = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    batch_ids: set[str] = set()
    stats = {"seen": 0, "weak": 0, "dupe": 0, "thin": 0}
    inserted = 0

    for i, topic in enumerate(TOPICS):
        if i:
            time.sleep(0.4)  # høflig mot Europe PMC
        try:
            raw = _fetch_pmc(topic_query(topic, from_date, today), MAX_FETCH_PER_TOPIC)
        except Exception as exc:
            print(f"  ✗  {topic['slug']}: feil ved henting — {exc}")
            continue

        cat_inserted = 0
        for r in raw:
            article = _normalize_pmc(r, topic["slug"])
            if not article:
                stats["thin"] += 1
                continue
            if article["id"] in batch_ids:
                continue  # samme studie traff en tidligere stoffspørring
            batch_ids.add(article["id"])
            if _is_blocked(seen, article["id"], today):
                stats["seen"] += 1
                continue

            primary, secondary = assign_topics(article["title"], article["abstract"])
            # Spørringen fant studien på dette stoffet, så stoffet er primært selv om
            # `terms` skulle bomme på skrivemåten i tittelen.
            if topic["slug"] not in primary:
                primary = [topic["slug"]] + primary
            article["topics_primary"] = primary
            article["topics_secondary"] = [s for s in secondary if s not in primary]

            score, why = _score_candidate(article)
            if score < MIN_SCORE:
                stats["weak"] += 1
                continue
            if _insert_scored(queue, article, score, why):
                cat_inserted += 1
            else:
                stats["dupe"] += 1

        inserted += cat_inserted
        if cat_inserted or raw:
            print(f"  ✓  {topic['slug']:20} {cat_inserted:3} nye i kø (av {len(raw)} hentet)")

    for n, label in ((stats["seen"], "allerede innrullet"),
                     (stats["dupe"], "sto allerede i køen"),
                     (stats["thin"], "manglet tittel/sammendrag"),
                     (stats["weak"], f"under terskel {MIN_SCORE}")):
        if n:
            print(f"      ({n} {label})")
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Bevis-gulvet — gratis, og for flere stoffer er det HELE oppslaget
#
# For et stoff uten en eneste kvalifiserende studie er det ingenting å syntetisere, og
# et tomt oppslag ville latt leseren tro at spørsmålet ikke er stilt. I stedet teller vi
# to tall: hvor mye som er publisert om stoffet i det hele tatt, og hvor mye av det som
# er en kontrollert menneskestudie. Avstanden mellom dem er svaret. Målt 22. august 2026:
# BPC-157 har 193 publikasjoner og 0 kontrollerte menneskestudier.
# ─────────────────────────────────────────────────────────────────────────────

def measure_evidence_floor(kb: dict, today: date, force: bool = False) -> int:
    """Mål bevis-gulvet for stoffer uten evidens i basen. To HTTP-kall per stoff, ingen
    Claude. Måles på nytt først etter FLOOR_RECHECK_DAYS — et nytt forsøk rekker ikke å
    bli indeksert på under en måned."""
    from_date = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    recheck_before = (today - timedelta(days=FLOOR_RECHECK_DAYS)).isoformat()
    measured = 0

    for t in TOPICS:
        topic = kb["topics"].get(t["slug"])
        if not topic or topic["studies"]:
            # Har stoffet evidens, er gulvet uinteressant — men vi rydder bort et gammelt
            # gulv så nettsiden ikke viser «0 kontrollerte studier» ved siden av fem.
            if topic and topic.get("evidence_floor") and topic["studies"]:
                topic["evidence_floor"] = None
            continue
        prev = topic.get("evidence_floor") or {}
        if not force and prev.get("checked", "") > recheck_before:
            continue

        raw = _hit_count(t["probe"] + " AND SRC:MED AND LANG:eng")
        time.sleep(1.0)
        strict = _hit_count(
            t["probe"] + _PMC_SUFFIX
            + f" AND (FIRST_PDATE:[{from_date} TO {today.isoformat()}])"
        )
        time.sleep(1.0)
        if raw is None or strict is None:
            print(f"  ⚠  {t['slug']}: kunne ikke måle bevis-gulvet (nettverksfeil)")
            continue

        topic["evidence_floor"] = {
            "checked": today.isoformat(),
            "published": raw,          # alt som er publisert om stoffet, noensinne
            "qualifying": strict,      # kontrollerte menneskestudier i vinduet
            "window_days": LOOKBACK_DAYS,
        }
        measured += 1
        print(f"  ⓘ  {t['slug']:20} {raw:5} publikasjoner → {strict:3} kontrollerte "
              f"menneskestudier siste {LOOKBACK_DAYS} dager")
    return measured


# ─────────────────────────────────────────────────────────────────────────────
# Claude — omtaler i batch
#
# Feilhåndteringen speiler textile_briefing.py og research_briefing.py, og av samme
# grunn: en tom stream er enten en transient hikke (prøv igjen) eller en
# sikkerhets-refusal (deterministisk — isolér problemabstractet med billige prober).
# ─────────────────────────────────────────────────────────────────────────────

def _writeup_system_prompt() -> str:
    return WRITEUP_SYSTEM_PROMPT.replace("{TOPIC_MENU}", _topic_menu())


def _build_user_content(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        hint = ", ".join(a.get("topics_primary") or []) or "—"
        lines.append(
            f"[{i}] {a['title']}\n"
            f"Tidsskrift: {a['journal']} | Publisert: {a['date']}\n"
            f"URL: {a['url']}\n"
            f"Stoffforslag fra lokal analyse (vurder selv): {hint}\n"
            f"Sammendrag: {a['abstract'][:MAX_ABSTRACT_CHARS]}\n"
            "---"
        )
    return (
        f"Dato: {datetime.now().strftime('%d. %B %Y')}\n\n"
        f"{len(articles)} studier om kosttilskudd, hentet fra Europe PMC og rangert "
        f"lokalt. Skriv en omtale av hver av dem:\n\n" + "\n".join(lines)
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
_TOPICS_RE = re.compile(r"^\*\*Stoffer:\*\*\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _parse_topics_line(block: str) -> list[str]:
    """Trekk **Stoffer:**-linjen ut av en omtale og valider mot registeret. Ukjente
    slugger forkastes stille — Claude finner av og til på et navn, og et stoff som ikke
    finnes ville blitt en død lenke. Blir det ingenting igjen, faller innrullingen
    tilbake på den lokale tildelingen."""
    m = _TOPICS_RE.search(block)
    if not m:
        return []
    out = []
    for part in re.split(r"[,;]", m.group(1)):
        slug = part.strip().strip("`*_ ").lower()
        if slug in TOPICS_BY_SLUG and slug not in out:
            out.append(slug)
    return out[:2]


def _parse_writeups(text: str, articles: list[dict]) -> tuple[dict[str, str], set[str]]:
    """Del Claudes svar i én blokk per studie og knytt hver blokk til sin studie.

    URL er primærnøkkelen: en feilmapping gir feil tekst under riktig tittel, som er
    verre enn en tapt dag.

    POSISJONSFALLBACK. Claude dropper av og til lenken i overskriften helt — den skriver
    `## Mysepulver gir litt mer benstyrke …` uten `[…](url)`. Under seedingen 22. august
    2026 skjedde det for HELE runde 3, og siden ingen blokk kunne knyttes til noen studie,
    ble åtte ferdigskrevne omtaler kastet og runden betalt for ingenting. Samme feilmodus
    er dokumentert for research_briefing.py, der `studiesForDay()` løser den i nettlaget.

    Fallbacken er trygg fordi den er dobbelt begrenset: den brukes bare når antallet
    blokker (omtaler + SKIP) stemmer nøyaktig med antallet studier vi sendte inn — da
    følger rekkefølgen prompten — og bare på plasser ingen URL allerede har krevd."""
    skipped: set[str] = set()
    by_url = {a["url"]: a for a in articles if a.get("url")}

    # 1) Del opp i rekkefølge, og hold på både URL-treff og posisjon.
    items: list[tuple[str, str | None]] = []   # (blokktekst, url eller None); SKIP = ("", url)
    for raw in re.split(r"\n(?=##\s)", text.strip()):
        block = raw.strip().strip("-").strip()
        if not block.startswith("##"):
            continue
        m = _SKIP_RE.match(block)
        if m:
            idx = int(m.group(1)) - 1
            url = articles[idx]["url"] if 0 <= idx < len(articles) else None
            if url:
                skipped.add(url)
            items.append(("", url))
            continue
        hit = next((u for u in by_url if u and u in block), None)
        items.append((block, hit))

    blocks: dict[str, str] = {url: block for block, url in items if block and url}

    # 2) Posisjonsfallback — kun ved eksakt antallsmatch, og kun på ledige plasser.
    if len(items) == len(articles):
        claimed = set(blocks) | skipped
        for i, (block, url) in enumerate(items):
            if not block or url:
                continue          # SKIP-linje, eller allerede mappet på URL
            target = articles[i]["url"]
            if target and target not in claimed:
                blocks[target] = block
                claimed.add(target)

    return blocks, skipped


def write_up_batch(queue: list[dict], batch_size: int) -> list[str]:
    """Få Claude til å skrive omtaler av de høyest scorede studiene som mangler tekst, og
    lagre dem I KØEN. Returnerer refused-id-er (kalleren persisterer dem).

    Alt som skrives her lagres — en studie koster tokens nøyaktig én gang, noensinne."""
    batch = [e for e in queue if e.get("status") == "scored"][:batch_size]
    if not batch:
        print("  ⚠  Ingen studier i kø å skrive om.")
        return []

    client = anthropic.Anthropic()
    pool = list(batch)
    refused_ids: list[str] = []

    print(f"\nSkriver omtaler av {len(pool)} studier med Claude (streamer svar)...\n")
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
                print("✗  Fant ingen enkeltstudie å fjerne — gir opp.")
                break
            drop = {id(a) for a in bad}
            for a in bad:
                print(f"    – fjernet: {a['title'][:90]}")
                refused_ids.append(a["id"])
            pool = [a for a in pool if id(a) not in drop]
            if not pool:
                print("✗  Ingen studier igjen etter filtrering — gir opp.")
                break
            print(f"  {len(pool)} studier igjen — prøver på nytt.")
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
        print("✗  Klarte ikke å knytte svaret til noen studie — ingenting lagret.")
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
    """Flytt de høyest scorede ferdigskrevne studiene fra køen inn i kunnskapsbasen.

    Studien lagres ÉN gang i `kb["studies"]`; stoffene refererer til den med id, så en
    studie som treffer to stoffer finnes fortsatt bare ett sted."""
    ready = [e for e in queue if e.get("status") == "ready" and e.get("writeup")][:limit]
    today = datetime.now().date().isoformat()

    for e in ready:
        topics = [s for s in (e.get("topics") or []) if s in TOPICS_BY_SLUG]
        if not topics:
            # Verken Claude eller den lokale tildelingen ga et gyldig stoff. Da hører
            # studien ikke hjemme noe sted, og vi oppretter ikke et hjemløst oppslag.
            e["status"] = "rejected"
            e["reject_reason"] = "ingen gyldige stoffer"
            continue

        kb["studies"][e["id"]] = {
            "id": e["id"], "doi": e.get("doi", ""), "url": e["url"], "title": e["title"],
            "journal": e.get("journal", "—"), "date": e.get("date", "—"),
            "category": e.get("category", ""), "writeup": e["writeup"],
            "topics": topics, "score": e.get("score", 0), "added": today,
        }
        for slug in topics:
            topic = kb["topics"].get(slug)
            if not topic:
                continue
            if e["id"] not in topic["studies"]:
                topic["studies"].insert(0, e["id"])
            topic["pending"] = topic.get("pending", 0) + 1
            topic["updated"] = today
            # Stoffet har fått evidens — bevis-gulvet er ikke lenger dets historie.
            topic["evidence_floor"] = None

    return ready


# ─────────────────────────────────────────────────────────────────────────────
# Stoffsyntese — Claude skriver om ett oppslag av gangen
#
# Kostnaden her vokser med basen, ikke med tilsiget, så to bremser: et stoff
# syntetiseres først når det har fått SYNTH_PENDING_MIN ny evidens, og maks
# MAX_SYNTH_PER_RUN stoffer per kjøring. Inputen kappes til de nyeste
# SYNTH_MAX_STUDIES omtalene.
# ─────────────────────────────────────────────────────────────────────────────

def topics_due_for_synthesis(kb: dict) -> list[dict]:
    """Stoffer som står for tur, viktigste først: de som aldri har fått tekst går foran
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
        f"STOFF: {topic['name']}  (slug: {topic['slug']}, gruppe: "
        f"{KIND_LABELS.get(topic['kind'], topic['kind'])})",
        f"BESKRIVELSE: {topic['blurb']}",
        f"DETTE MARKEDSFØRES STOFFET SOM: {topic.get('claim', '—')}",
        f"ANTALL STUDIER UNDER STOFFET: {len(topic['studies'])}"
        + (f" (de {len(ids)} nyeste vises under)" if len(topic["studies"]) > len(ids) else ""),
    ]
    if prev:
        parts.append(
            f"\nFORRIGE OPPSLAG (skal erstattes, ikke bygges videre på ordrett):\n{prev}")
    parts.append("\nEVIDENS:\n\n" + "\n\n---\n\n".join(blocks))
    return "\n".join(parts)


def _parse_synth(text: str) -> dict | None:
    """Plukk JSON-objektet ut av svaret. Claude legger av og til på en kodeblokk eller en
    innledende setning, så vi tar første balanserte {...} og validerer feltene mot
    registeret. Kan svaret ikke tolkes, returneres None og stoffet står uendret — et halvt
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

    dose = []
    for d in data.get("dose") or []:
        if isinstance(d, str) and d.strip():
            dose.append({"text": d.strip(), "strength": "moderat"})
        elif isinstance(d, dict) and (d.get("text") or "").strip():
            dose.append({
                "text": d["text"].strip(),
                "strength": d.get("strength") if d.get("strength") in STRENGTHS else "moderat",
            })

    def strlist(key, limit):
        return [s.strip() for s in (data.get(key) or [])
                if isinstance(s, str) and s.strip()][:limit]

    return {
        "summary": data["summary"].strip(),
        "measured": (data.get("measured") or "").strip(),
        "verdict": data.get("verdict") if data.get("verdict") in VERDICTS else "ukjent",
        "confidence": data.get("confidence") if data.get("confidence") in STRENGTHS else "svak",
        "who": (data.get("who") or "").strip(),
        "dose": dose[:3],
        "interactions": strlist("interactions", 3),
        "changes_verdict": strlist("changes_verdict", 3),
    }


def synthesize_topic(client, kb: dict, topic: dict) -> bool:
    """Skriv ett stoffoppslag på nytt. Returnerer True hvis KB-en ble oppdatert."""
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
        print("    ✗ avvist av sikkerhetsklassifikatoren — stoffet står uendret.")
        return False

    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    parsed = _parse_synth(text)
    if not parsed:
        print("    ✗ kunne ikke tolke svaret som JSON — stoffet står uendret.")
        return False

    topic.update(parsed)
    topic["pending"] = 0
    topic["synth_at"] = datetime.now().date().isoformat()
    topic["updated"] = topic["synth_at"]
    print(f"    ✓ {VERDICTS[topic['verdict']].lower()} ({topic['confidence']} evidens), "
          f"{len(topic['dose'])} dosering(er), "
          f"{len(topic['changes_verdict'])} ting som ville endret dommen")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Hovedprogram
# ─────────────────────────────────────────────────────────────────────────────

def _print_kb_state(kb: dict) -> None:
    topics = [t for t in kb["topics"].values() if not t.get("retired")]
    with_text = sum(1 for t in topics if (t.get("summary") or "").strip())
    floors = sum(1 for t in topics if (t.get("evidence_floor") or {}).get("checked"))
    doses = sum(len(t.get("dose") or []) for t in topics)
    print(f"Kunnskapsbase: {len(kb['studies'])} studier, "
          f"{with_text}/{len(topics)} stoffer konkludert, {doses} doseringer, "
          f"{floors} med målt bevis-gulv.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tilskudds-kunnskapsbase med Claude")
    parser.add_argument("--dry-run", action="store_true",
                        help="Hent, score, mål bevis-gulv og fyll køen uten å kalle "
                             "Claude. Gratis, og eneste trygge måte å teste spørringene på.")
    parser.add_argument("--seed", action="store_true",
                        help="Engangs oppstart: skriv mange omtaler og syntetiser mange "
                             "stoffer i én kjøring. Koster vesentlig mer enn en vanlig dag.")
    parser.add_argument("--writeups", type=int, default=None,
                        help="Overstyr antall omtaler Claude skriver denne kjøringen.")
    parser.add_argument("--synth", type=int, default=None,
                        help="Overstyr antall stoffer som syntetiseres denne kjøringen.")
    parser.add_argument("--floor", action="store_true",
                        help="Mål bevis-gulvet på nytt for alle stoffer uten evidens, "
                             "uavhengig av når det sist ble målt.")
    args = parser.parse_args()

    _load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY") and not args.dry_run:
        print("Feil: ANTHROPIC_API_KEY er ikke satt.")
        sys.exit(1)

    today = datetime.now().date()
    print(f"\n{'─'*70}")
    print(f"  Tilskudds-kunnskapsbase  —  {datetime.now().strftime('%A %d. %B %Y')}")
    print(f"{'─'*70}\n")

    writeup_rounds = 1
    n_writeups = args.writeups if args.writeups is not None else WRITEUP_BATCH_SIZE
    n_rollin = MAX_ROLLIN_PER_RUN
    n_synth = args.synth if args.synth is not None else MAX_SYNTH_PER_RUN
    if args.seed:
        writeup_rounds = 8          # 8 × 8 = opptil 64 omtaler
        n_rollin = 80
        n_synth = args.synth if args.synth is not None else 25

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
        print(f"  ⓘ  {counts['scored']} studier i kø (≥ {need}) — hopper over henting.")
    else:
        print(f"\nHenter fra Europe PMC, ett søk per stoff, siste {LOOKBACK_DAYS} dager...")
        refill_queue(queue, seen, today)
        _save_queue(queue)
        counts = _queue_counts(queue)
        print(f"  → kø: {counts['ready']} ferdige, {counts['scored']} venter på tekst")

    # ── 4. Bevis-gulv ────────────────────────────────────────────────────────
    # Gratis, og for stoffene i gruppen `uavklart` er dette hele oppslaget.
    print("\nMåler bevis-gulv for stoffer uten evidens...")
    if measure_evidence_floor(kb, today, force=args.floor):
        _save_kb(kb)
    else:
        print("  ⓘ  Ingen stoffer trengte ny måling.")

    if args.dry_run:
        print("\n  ⓘ  --dry-run: hopper over Claude, ruller ikke inn, syntetiserer ikke.")
        top = sorted(queue, key=lambda e: -e.get("score", 0))[:15]
        print(f"\nTopp {len(top)} i køen:")
        for e in top:
            print(f"  {e.get('score', 0):5.1f}  "
                  f"[{','.join(e.get('topics_primary') or []) or '—'}]  {e['title'][:66]}")
        sys.exit(0)

    # ── 5. Claude skriver omtaler ────────────────────────────────────────────
    refused_ids: list[str] = []
    for round_i in range(writeup_rounds):
        counts = _queue_counts(queue)
        if counts["ready"] >= WRITEUP_REFILL_BELOW and not args.seed:
            print(f"\n  ⓘ  {counts['ready']} ferdigskrevne i kø (≥ {WRITEUP_REFILL_BELOW}) "
                  "— hopper over Claude-kallet. Gratis dag.")
            break
        if counts["scored"] == 0:
            print("\n  ⚠  Ingen studier i kø å skrive om.")
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

    # ── 6. Innrulling ────────────────────────────────────────────────────────
    rolled = roll_into_kb(kb, queue, n_rollin)
    ok = [e for e in rolled if e.get("status") != "rejected"]
    if ok:
        print(f"\nRullet inn {len(ok)} studier i kunnskapsbasen:")
        for e in ok:
            print(f"    {e.get('score', 0):5.1f}  [{','.join(e['topics'])}]  {e['title'][:60]}")
        _save_seen(seen, [e["id"] for e in ok], [])
        done = {e["id"] for e in ok}
        queue = [e for e in queue if e["id"] not in done]
    else:
        print("\n  ⚠  Ingen ferdigskrevne studier å rulle inn.")
    _save_queue(queue)
    _save_kb(kb)

    # ── 7. Syntese ───────────────────────────────────────────────────────────
    due = topics_due_for_synthesis(kb)
    if not due:
        print("\n  ⓘ  Ingen stoffer har nok ny evidens til å skrives om. Gratis syntese.")
    else:
        print(f"\n{len(due)} stoffer står for tur — syntetiserer {min(n_synth, len(due))}:")
        client = anthropic.Anthropic()
        for topic in due[:n_synth]:
            if synthesize_topic(client, kb, topic):
                _save_kb(kb)   # lagre etter hvert stoff: en feil senere skal ikke koste de før

    _save_kb(kb)
    print()
    _print_kb_state(kb)
    left = _queue_counts(queue)
    print(f"Kø: {left['ready']} ferdigskrevne igjen, {left['scored']} venter på tekst.")
    still_due = len(topics_due_for_synthesis(kb))
    if still_due:
        print(f"  ⓘ  {still_due} stoffer venter fortsatt på syntese "
              f"(~{-(-still_due // max(n_synth, 1))} kjøringer).")


if __name__ == "__main__":
    main()
