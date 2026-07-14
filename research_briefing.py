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
MAX_TOKENS = 8192

# Vindu på publiseringsdato. Forskning har ingen nyhetssyklus — en metaanalyse fra april er
# like relevant som en fra i går — så vi jakter ikke på det ferskeste, men på det BESTE vi
# ikke har vist før. Et bredt vindu er dessuten et *krav* for kvalitetsfiltrene under:
# Europe PMC tildeler MeSH-termer og publikasjonstyper uker etter publisering, så en artikkel
# som er to dager gammel er ennå ikke merket som menneskestudie eller RCT. Målt på `exercise`:
# 2 dager → 0 treff med MESH:"Humans"; 30 dager → 24; 180 dager → rikelig.
# 365 dager gir ~1 130 studier i poolen (~3 nye i døgnet) — nok til å levere daglig i årevis.
LOOKBACK_DAYS = 365

MAX_ITEMS = 5              # maks studier i briefen (styres også i SYSTEM_PROMPT)
RAW_POOL = 100             # rå kandidater hentet PER KATEGORI (før lokal scoring)
CANDIDATE_POOL = 6         # kandidater PER KATEGORI som sendes til Claude (etter scoring)
MAX_ABSTRACT_CHARS = 1200  # maks tegn fra hvert abstract som sendes til Claude

# Robusthet mot tomt/mislykket Claude-svar (transient API-hikke gir noen ganger 0 tegn)
CLAUDE_MAX_ATTEMPTS = 3    # antall forsøk hvis streamen kommer TOM (transient hikke)
CLAUDE_RETRY_DELAY = 5     # sekunder mellom forsøk

# Robusthet mot Claudes sikkerhetsklassifikator: enkelte medisinske abstracts
# (typisk bio-relatert innhold) trigger `stop_reason == "refusal"`, som stopper HELE
# batchen. En refusal er deterministisk — retry hjelper ikke. I stedet isolerer vi
# problemabstractene med billige probe-kall og kjører oppsummeringen på nytt uten dem.
CLAUDE_REFUSAL_MAX_ROUNDS = 4   # maks antall isoler-og-fjern-runder før vi gir opp
CLAUDE_PROBE_MAX_TOKENS = 16    # små kall kun for å avgjøre refusal (ja/nei)

# Dedup mot gjentakelser på tvers av dager. Tre nivåer, fordi et 180-dagers vindu ellers ville
# servert de samme toppkandidatene dag etter dag:
#   • valgt av Claude (picked)      → aldri vist igjen (SEEN_RETENTION_DAYS)
#   • avvist av sikkerhetsklassifikatoren (refused) → blokkert like lenge som picked. En
#     refusal er deterministisk, så å sende abstractet igjen etter karantenen ville bare
#     utløst en ny (dyr) isoler-og-fjern-runde. Lagres OGSÅ når kjøringen gir opp helt.
#   • sendt, men ikke valgt         → karantene (UNPICKED_COOLDOWN_DAYS), så den ikke brenner
#                                     input-tokens hver dag, men kan komme tilbake senere
SEEN_FILE = "research_seen_dois.json"
SEEN_RETENTION_DAYS = 400
UNPICKED_COOLDOWN_DAYS = 14  # kort: poolen er liten (~1 100), gode studier skal få komme igjen

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
# (~1 130 studier i et 365-dagers vindu, ~3 nye i døgnet) — rikelig når vi viser 5 om dagen.
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

Du får en liste med kandidatstudier (kategori, tittel, tidsskrift, dato, URL, engelsk sammendrag). Alle er allerede menneskestudier av typen RCT, metaanalyse eller systematisk oversikt — utvelgelsen din handler derfor om RELEVANS og TYDELIGHET, ikke om å luke bort dyrestudier.

Velg de OPPTIL 5 mest verdifulle. Heller tre sterke enn fem der to er tynne. Hvis ingen er gode nok, skriv kun: "Ingen vesentlige nye studier i dag."

UTVALGSKRITERIER (prioritert rekkefølge):
1. Handlingsrom — kan leseren faktisk gjøre noe med dette selv (trene annerledes, spise annerledes, sove annerledes)? Klinisk behandling han aldri vil ta stilling til, velges bort.
2. Tydelige tall — studien må rapportere konkrete effektstørrelser (prosent, HR/RR/OR med konfidensintervall, SMD, absolutte endringer). Studier som kun sier "signifikant bedring" uten tall, velges bort.
3. Betydning for lang og frisk levetid — harde utfall (dødelighet, hjerte-kar, diabetes, muskelmasse, kognisjon, søvnkvalitet) foran surrogatmål.
4. Robusthet — store metaanalyser og RCT-er med mange deltakere foran små studier.
5. Variasjon — unngå fem studier om nesten det samme. Spre gjerne over kategoriene, men aldri på bekostning av kvalitet.

FORMAT — for hver valgte studie, nøyaktig denne strukturen:
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
    """Skal denne DOI-en holdes utenfor dagens kandidatpool?

    Valgt tidligere → blokkert helt ut SEEN_RETENTION_DAYS (leseren skal aldri se samme
    studie to ganger). Avvist av sikkerhetsklassifikatoren → blokkert like lenge (en
    refusal er deterministisk; å prøve igjen koster bare en ny bisect-runde). Sendt til
    Claude uten å bli valgt → blokkert i UNPICKED_COOLDOWN_DAYS, så den slutter å brenne
    input-tokens hver dag, men kan komme tilbake senere."""
    entry = seen.get(doi)
    if not entry:
        return False
    long_block = entry["picked"] or entry.get("refused")
    days = SEEN_RETENTION_DAYS if long_block else UNPICKED_COOLDOWN_DAYS
    cutoff = ((today or datetime.now().date()) - timedelta(days=days)).isoformat()
    return entry["last"] >= cutoff


def _save_seen(
    seen: dict,
    sent_dois: list[str],
    picked_dois: list[str],
    refused_dois: list[str] | None = None,
) -> None:
    """Merk dagens kandidater som sett. `sent_dois` er alle som ble sendt til Claude,
    `picked_dois` de som faktisk havnet i briefingen, `refused_dois` de som ble fjernet
    fordi de trigget sikkerhetsklassifikatoren. picked/refused er klebrige — en DOI som
    først var sendt-og-forkastet og senere blir valgt (eller avvist), oppgraderes."""
    today = datetime.now().date().isoformat()
    picked = set(picked_dois)
    refused = set(refused_dois or [])
    for doi in sent_dois:
        if not doi:
            continue
        prev = seen.get(doi, {})
        seen[doi] = {
            "last": today,
            "picked": doi in picked or prev.get("picked", False),
            "refused": doi in refused or prev.get("refused", False),
        }

    # Prun oppføringer som ikke lenger kan blokkere noe (ISO-datoer sammenlignes som tekst).
    keep_from = (
        datetime.now().date() - timedelta(days=max(SEEN_RETENTION_DAYS, UNPICKED_COOLDOWN_DAYS))
    ).isoformat()
    seen = {doi: e for doi, e in seen.items() if e["last"] >= keep_from}
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


# ─────────────────────────────────────────────────────────────────────────────
# Lokal scoring — grovsorteringen skjer HER, ikke hos Claude
#
# Europe PMC-spørringen garanterer allerede menneskestudie + RCT/metaanalyse/oversikt.
# Scoringen rangerer det som er igjen etter det leseren faktisk er ute etter: tydelige tall,
# harde utfall, og noe han kan gjøre selv. Kun topp CANDIDATE_POOL per kategori sendes til
# Claude — det kutter input fra ~32 000 til ~9 000 tokens/dag og gjør at Claude bruker
# kapasiteten sin på å FORKLARE i stedet for å lete.
# ─────────────────────────────────────────────────────────────────────────────

MIN_SCORE = 3.0  # kandidater under dette forkastes helt — heller færre enn svake

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


def fetch_research() -> list[dict]:
    """Hent og rangér kandidatstudier per kategori.

    Per kategori: hent RAW_POOL nyest indekserte studier som matcher de harde filtrene,
    fjern det leseren allerede har sett (og det som ligger i karantene), score lokalt, og
    behold topp CANDIDATE_POOL. Duplikater på tvers av kategoriene beholdes kun én gang —
    første kategori vinner."""
    import time as _time

    today = datetime.now().date()
    start = today - timedelta(days=LOOKBACK_DAYS)
    date_filter = f" AND (FIRST_PDATE:[{start.isoformat()} TO {today.isoformat()}])"

    seen = _load_seen()
    articles: list[dict] = []
    picked_ids: set[str] = set()
    skipped_seen = 0
    skipped_weak = 0

    for ci, (category, cat_query) in enumerate(CATEGORY_QUERIES.items()):
        if ci:
            _time.sleep(1)  # høflig mot Europe PMC
        params = {
            "query": cat_query + date_filter,
            "resultType": "core",
            "sort": "P_PDATE_D desc",  # nyest indekserte først — poolen roterer av seg selv
            "pageSize": str(RAW_POOL),
            "format": "json",
        }
        try:
            resp = httpx.get(_API_URL, params=params, headers=_HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"  ✗  Europe PMC ({category}): feil ved henting — {exc}")
            continue

        scored: list[tuple[float, str, dict]] = []
        for r in data.get("resultList", {}).get("result", []):
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

            article = {
                "category": category,
                "title": title,
                "abstract": abstract[:MAX_ABSTRACT_CHARS],
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
            scored.append((score, why, article))

        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[:CANDIDATE_POOL]
        print(f"  ✓  {category}: {len(top)} kandidater (av {len(scored)} over terskel)")
        for score, why, article in top:
            print(f"       {score:5.1f}  {article['title'][:72]}")
            print(f"              ({why})")
            articles.append(article)

    if skipped_seen:
        print(f"  ({skipped_seen} allerede vist eller i karantene — hoppet over)")
    if skipped_weak:
        print(f"  ({skipped_weak} forkastet av lokal scoring — under terskel {MIN_SCORE})")
    return articles


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
            f"Sammendrag: {a['abstract']}\n"
            "---"
        )
    return "\n".join(lines)


def _build_user_content(articles: list[dict]) -> str:
    today_str = datetime.now().strftime("%A %d. %B %Y")
    return (
        f"Dato: {today_str}\n\n"
        f"{len(articles)} kandidatstudier, forhåndsfiltrert til menneskestudier "
        f"(RCT / metaanalyse / systematisk oversikt) fra de siste {LOOKBACK_DAYS} dagene. "
        f"Velg opptil {MAX_ITEMS} av dem:\n\n"
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


def summarize_research_with_claude(articles: list[dict]) -> tuple[str, list[str]]:
    """Returnerer (briefing, refused_dois). `refused_dois` er DOI-ene til abstracts som
    ble fjernet fordi de trigget sikkerhetsklassifikatoren — kalleren skal persistere dem
    (refused-flagget) uansett om briefingen lyktes, så de aldri sendes inn igjen."""
    client = anthropic.Anthropic()  # leser ANTHROPIC_API_KEY automatisk fra env

    pool = list(articles)
    refused_dois: list[str] = []

    print("\nVelger og oppsummerer forskning med Claude (streamer svar)...\n")
    print("─" * 70)

    # To feilmoduser håndteres ulikt:
    #  • TOM streng uten refusal  → transient hikke; prøv på nytt (samme input).
    #  • stop_reason == "refusal" → sikkerhetsklassifikatoren stoppet batchen;
    #    deterministisk, så retry er nytteløst. Isolér og fjern problemabstract(er),
    #    kjør så på nytt med det rensede settet.
    # Et ekte «ingen gode studier»-svar er teksten "Ingen vesentlige nye studier
    # i dag.", ikke en tom streng, så blank tekst er alltid en feil.
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

    print(
        f"Henter forskning fra Europe PMC — menneskestudier (RCT/metaanalyse/oversikt), "
        f"siste {LOOKBACK_DAYS} dager..."
    )
    articles = fetch_research()

    if not articles:
        print("\nIngen nye studier funnet (eller alle allerede dekket). Avslutter.")
        sys.exit(0)

    print(f"\n  {len(articles)} kandidatstudier sendes til Claude.")

    briefing, refused_dois = summarize_research_with_claude(articles)

    print("─" * 70)

    # Guard: et tomt svar (etter alle retry-forsøk) skal ALDRI lagres — det ville
    # overskrevet dagsfilen med en blank research_md og feilaktig se ut som en
    # stille dag. Avslutt uten å skrive; feltet utelates da for dagen (myk feil,
    # jf. øvrige seksjoner), og dedup-cachen røres ikke så studiene kan velges i morgen.
    # Unntak: abstracts som trigget sikkerhetsklassifikatoren persisteres LIKEVEL med
    # refused-flagget — en refusal er deterministisk, og uten dette ville nøyaktig samme
    # pool kommet tilbake i morgen og betalt hele isoler-og-fjern-runden på nytt.
    if not briefing.strip():
        if refused_dois:
            _save_seen(_load_seen(), refused_dois, [], refused_dois)
            print(f"  ⓘ  {len(refused_dois)} avvist(e) abstract(s) merket refused — "
                  "sendes aldri inn igjen.")
        print("\n✗  Tomt svar fra Claude etter alle forsøk — lagrer IKKE tom "
              "forskningsbriefing. Feltet utelates for i dag.")
        sys.exit(1)

    # Marker dagens kandidater som sett. Valgte studier (URL-en dukker opp i briefingen)
    # blokkeres for godt; avviste (refusal) blokkeres like lenge; de øvrige som ble sendt
    # til Claude, får karantene — ellers ville de samme toppkandidatene blitt sendt inn
    # på nytt hver eneste dag.
    picked = [a["doi"] for a in articles if a["doi"] and a["url"] and a["url"] in briefing]
    sent = [a["doi"] for a in articles if a["doi"]]
    _save_seen(_load_seen(), sent, picked, refused_dois)

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
