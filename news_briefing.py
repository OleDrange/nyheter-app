#!/usr/bin/env python3
"""
news_briefing.py  —  Daglig nyhetsbriefing med Claude AI og Notion-publisering

Kjør:
    python news_briefing.py            # print til terminal
    python news_briefing.py --save     # lagrer også som markdown-fil

Miljøvariabler som må være satt:
    ANTHROPIC_API_KEY           — påkrevd
    NOTION_API_KEY              — valgfri (for Notion-publisering)
    NOTION_PARENT_PAGE_ID       — valgfri (ID på Notion-siden å opprette undersider under)
"""

import os
import re
import sys
import time
import json
import argparse
from datetime import datetime, timezone, timedelta

# Sørg for at terminalen håndterer UTF-8 (nødvendig på Windows)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import httpx
import feedparser
import anthropic


def _load_dotenv() -> None:
    """Les .env-filen i samme mappe som scriptet og sett manglende env-variabler."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except FileNotFoundError:
        pass


def store_briefing(date_str, *, news_md=None, research_md=None,
                   weather=None, weather_alt=None, market=None,
                   research_items=None, quiz=None, riddles=None,
                   learning=None, brann=None, reflection=None) -> None:
    """Skriv/merge dagens briefing til <BRIEFING_DATA_DIR>/briefings/<date>.json.

    Begge scriptene (nyhet + forskning) skriver inn i samme dagsfil — kun feltene
    den enkelte kjøringen produserte oppdateres. Skrivingen er atomisk (skriv til
    .tmp og rename) slik at web-tjenesten aldri leser en halvskrevet fil.
    """
    data_dir = os.environ.get("BRIEFING_DATA_DIR", ".")
    out_dir = os.path.join(data_dir, "briefings")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{date_str}.json")

    data: dict = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {}

    data["date"] = date_str
    data["created_at"] = datetime.now().isoformat()
    if news_md is not None:
        data["news_md"] = news_md
    if research_md is not None:
        data["research_md"] = research_md
    if weather is not None:
        data["weather"] = weather
    if weather_alt is not None:
        data["weather_alt"] = weather_alt
    if market is not None:
        data["market"] = market
    if research_items is not None:
        data["research_items"] = research_items
    if quiz is not None:
        data["quiz"] = quiz
    if riddles is not None:
        data["riddles"] = riddles
    if learning is not None:
        data["learning"] = learning
    if brann is not None:
        data["brann"] = brann
    if reflection is not None:
        data["reflection"] = reflection

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # atomisk publisering
    print(f"✓  Lagret til datalager: {path}")


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — legg til / fjern RSS-feeds her
# ─────────────────────────────────────────────────────────────────────────────

RSS_FEEDS: dict[str, str] = {
    "NRK Nyheter": "https://www.nrk.no/toppsaker.rss",
    "NRK Siste": "https://www.nrk.no/nyheter/siste.rss",
    "Bergens Tidende": "https://www.bt.no/rss.xml",
    "E24": "https://e24.no/rss2/",
    "E24 Børs og finans": "https://e24.no/rss2/?seksjon=boers-og-finans",
    "The Guardian World": "https://www.theguardian.com/world/rss",
    "The Guardian Business": "https://www.theguardian.com/business/rss",
    "BBC World": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "BBC Business": "http://feeds.bbci.co.uk/news/business/rss.xml",
    "Dagens Næringsliv": "https://services.dn.no/api/feed/rss/",
    # AI og teknologi
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "MIT Technology Review": "https://www.technologyreview.com/feed/",
    # Forskning og vitenskap
    "ScienceDaily": "https://www.sciencedaily.com/rss/top/science.xml",
    "Nature News": "https://www.nature.com/nature.rss",
    "Phys.org": "https://phys.org/rss-feed/",
    "Titan (UiO)": "https://titan.uio.no/rss.xml",
    "Aftenposten Viten": "https://www.aftenposten.no/rss/viten",
    # Helse og medisin
    "ScienceDaily Helse": "https://www.sciencedaily.com/rss/health_medicine.xml",
    "STAT News": "https://www.statnews.com/feed/",
}

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
LOOKBACK_HOURS = 24
MAX_PER_FEED = 25  # maks antall artikler per kilde
MAX_DESC_CHARS = 300  # maks tegn fra ingress/beskrivelse per artikkel
NEWS_HISTORY_DAYS = 2  # dedup: ikke gjenta saker fra briefingene de siste N dagene

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
}

SYSTEM_PROMPT = """Nyhetsbriefing på norsk for en smart allmennleser i Bergen med interesse for økonomi. Skriv som en Bloomberg-terminal: tall og fakta, null pynt — men alt skal kunne forstås uten å måtte søke opp begreper.

FORKLAR UNDERVEIS:
- Fagbegreper, forkortelser og mindre kjente selskaper/institusjoner forklares kort inne i punktet (2–6 ord): «styringsrenten (Norges Banks utlånsrente)», «brikkeprodusenten TSMC», «EMA (EUs legemiddelmyndighet)».
- Allment kjente navn (Apple, Google, NATO, Norges Bank) trenger ingen forklaring.

FORMAT:
- Syv seksjoner med ## heading og • kulepunkter — ingenting annet.
- Maks 3 punkter per seksjon, med unntak der lavere maks er angitt. Heller færre enn å fylle opp med svake nyheter.
- Én, maks to setninger per punkt. Subjekt + verb + tall/konsekvens. Setning to brukes kun til å forklare et begrep eller hvorfor saken betyr noe.
- Alltid inline-lenke i nyhetspunkter: [tittel](url)
- Tom nyhetseksjon → skriv kun: • Ingen viktige hendelser.

GJENTAKELSE:
- Saker listet under «DEKKET I BRIEFINGENE DE SISTE DAGENE» i meldingen skal ikke gjentas.
- Unntak: vesentlig ny utvikling (nye tall, vedtak, eskalering) — da skal punktet handle om det som er nytt, ikke resirkulere gårsdagens vinkel.

FORBUDT I OUTPUT:
- Fyllfraser: "Det er verdt å merke seg", "I tillegg", "Som et resultat", "Det er viktig å"
- Gjentakelse av kildenavn, dato eller kontekst fra forrige punkt
- Vurderinger og adjektiver som ikke er tall: "betydelig", "kraftig", "stor"

KUTT ALLTID: sport, kjendis, krim, underholdning, vær, lokale ulykker, innenrikspolitikk uten direkte markedseffekt, eiendomsmarkedet.

## 🏥 Helse og medisin
Kilder: ScienceDaily Helse, STAT News — og helserelaterte artikler fra øvrige kilder.
Ta med: nye behandlingsmetoder med klinisk evidens, legemiddelgodkjenninger (FDA/EMA), forskningsgjennombrudd med direkte pasientkonsekvens, folkehelsevarsler.
Kutt: kostholdstips, treningsråd, enkeltcase-studier uten generell relevans.

## 🔬 Forskning og vitenskap
Kilder: ScienceDaily, Scientific American, MIT Technology Review — og vitenskapsnyheter fra øvrige kilder.
Ta med: store vitenskapelige gjennombrudd (fysikk, kjemi, biologi, romfart), klima- og energiforskning med konkrete tall eller milepæler, ny teknologi med bred samfunnskonsekvens.
Kutt: inkrementelle fremskritt, akademiske artikler uten praktisk konsekvens.

## 🤖 AI, teknologi og startups
Kilder: VentureBeat AI, MIT Technology Review — og teknologinyheter fra øvrige kilder.
Ta med: nye AI-modeller/versjoner (GPT, Claude, Gemini osv.), AI-regulering, store nyheter fra Apple/Google/Meta/Microsoft, produktlanseringer med markedseffekt.
Startups og VC: maks 1 punkt — kun finansieringsrunder over 100 MUSD eller strategisk viktige oppkjøp.
Kutt: produktanmeldelser, hype uten konkret nyhet.

## 🌍 Internasjonalt
Ta med KUN det viktigste: krig/konflikt med geopolitisk spillover, store naturkatastrofer, valg/regjeringsskifte i G20.
Maks 1 punkt.

## 🇳🇴 Norsk økonomi
Ta med: Norges Bank, statsbudsjett, norske selskaper med markedseffekt, oljesektor, kronekurs med årsak.
Kutt: innenrikspolitikk uten økonomisk utfall, eiendomsmarkedet.

## 📈 Marked og makro
Markedsdata (priser og prosentendringer) vises allerede i et eget snapshot øverst — IKKE gjenta prisene.
Ta med: rentevedtak, inflasjon, handelskrig, HVORFOR markedet beveget seg, kvartalstall som beveger markedet.
Krypto: maks 1 punkt — kun ved bevegelse over 10 % eller regulatorisk hendelse av betydning.
Kutt: dagsbevegelser uten nyhet bak.

## 🏙️ Bergen og Vestland
Ta med KUN direkte hverdagskonsekvens:
✓ Kollektivstreik/-stans (Skyss, Bybanen, buss)
✓ Veistenging / store trafikkforstyrrelser
✓ Lokale prisendringer (kommunale avgifter)
✓ Kommunevedtak (barnehage, skole, helse)
✓ Helseadvarsler / sykehuskapasitet
✓ Store arbeidsplassnyheter (nedleggelse / nyetablering)

TOTALBUDSJETT: Maks 550 ord for alle syv seksjoner samlet."""

# ─────────────────────────────────────────────────────────────────────────────
# Værvarsling Bergen (Yr / MET Norway API)
# ─────────────────────────────────────────────────────────────────────────────

_YR_URL = (
    "https://api.met.no/weatherapi/locationforecast/2.0/complete?lat={lat}&lon={lon}"
)

# Steder i værpanelet på nettsiden. «bergen» er hovedstedet: lagres som
# `weather`-feltet og brukes i terminal/Notion. De øvrige lagres i `weather_alt`.
WEATHER_LOCATIONS: dict[str, dict] = {
    "bergen": {"name": "Bergen", "lat": 60.3928, "lon": 5.3241},
    "oslo": {"name": "Oslo", "lat": 59.9139, "lon": 10.7522},
    "alicante": {"name": "Alicante", "lat": 38.3452, "lon": -0.4810},
}

_WEATHER_DAYS_AHEAD = 6  # dagsvarsler utover i dag (7 dager totalt)

_SYMBOL_NO: dict[str, str] = {
    "clearsky": "klarvær",
    "fair": "lettskyet",
    "partlycloudy": "delvis skyet",
    "cloudy": "skyet",
    "fog": "tåke",
    "lightrain": "lett regn",
    "lightrainshowers": "lette regnbyger",
    "rain": "regn",
    "rainshowers": "regnbyger",
    "heavyrain": "kraftig regn",
    "heavyrainshowers": "kraftige regnbyger",
    "lightsleet": "lett sludd",
    "sleet": "sludd",
    "sleetshowers": "sluddbyger",
    "lightsnow": "lett snø",
    "snow": "snø",
    "snowshowers": "snøbyger",
    "thunder": "torden",
    "rainandthunder": "regn og torden",
    "heavyrainandthunder": "kraftig regn og torden",
}


def _symbol_no(code: str) -> str:
    base = re.sub(r"_(day|night|polartwilight)$", "", code)
    return _SYMBOL_NO.get(base, base)


# Rangering brukt som tie-break ved valg av periodesymbol (morgen/ettermiddag/
# kveld): det VANLIGSTE symbolet i perioden vinner, og ved likt antall velges
# det mest optimistiske (lavest indeks her) — sol skal ikke gjemmes bak en
# enslig skyet time.
_SYMBOL_SEVERITY: list[str] = [
    "clearsky", "fair", "partlycloudy", "cloudy", "fog",
    "lightrainshowers", "lightrain", "rainshowers", "rain",
    "lightsleet", "sleetshowers", "sleet",
    "lightsnow", "snowshowers", "snow",
    "heavyrainshowers", "heavyrain",
    "thunder", "rainandthunder", "heavyrainandthunder",
]


def _severity(code: str | None) -> int:
    if not code:
        return -1
    base = re.sub(r"_(day|night|polartwilight)$", "", code)
    try:
        return _SYMBOL_SEVERITY.index(base)
    except ValueError:
        return len(_SYMBOL_SEVERITY)


def _build_daily(ts: list, today) -> list[dict]:
    """Aggreger MET-timeserien til dagsvarsler for i dag + _WEATHER_DAYS_AHEAD
    dager frem: min/maks temp, nedbørsum, maks vind/vindkast/UV, tre
    periodesymboler (morgen/ettermiddag/kveld) og detaljrader i `hours`
    (1-timesoppløsning de første ~2 døgnene, deretter 6-timersblokker —
    mer gir ikke MET-API-et så langt frem)."""
    days: dict = {}
    covered_until = None  # hindrer dobbelttelling av nedbør i 1t→6t-overgangen

    for entry in ts:
        t_local = datetime.fromisoformat(
            entry["time"].replace("Z", "+00:00")
        ).astimezone()
        day = t_local.date()
        if day < today:
            continue
        if (day - today).days > _WEATHER_DAYS_AHEAD:
            break

        d = entry["data"]
        inst = d["instant"]["details"]
        span = window = None
        if "next_1_hours" in d:
            span, window = 1, d["next_1_hours"]
        elif "next_6_hours" in d:
            span, window = 6, d["next_6_hours"]

        b = days.setdefault(
            day,
            {
                "date": day.isoformat(),
                "min_temp": None, "max_temp": None,
                "precip": 0.0,
                "max_wind": None, "max_gust": None, "max_uv": None,
                "_periods": [{}, {}, {}],  # base-symbol → [antall, full kode]
                "hours": [],
            },
        )

        temp = inst.get("air_temperature")
        wind = inst.get("wind_speed")
        gust = inst.get("wind_speed_of_gust")
        uv = inst.get("ultraviolet_index_clear_sky")

        # Min/maks temp — for 6-timersblokker finnes egne min/maks i vinduet
        candidates = [temp]
        if span == 6:
            candidates += [
                window["details"].get("air_temperature_min"),
                window["details"].get("air_temperature_max"),
            ]
        for c in candidates:
            if c is None:
                continue
            if b["min_temp"] is None or c < b["min_temp"]:
                b["min_temp"] = c
            if b["max_temp"] is None or c > b["max_temp"]:
                b["max_temp"] = c

        for key, val in (("max_wind", wind), ("max_gust", gust), ("max_uv", uv)):
            if val is not None and (b[key] is None or val > b[key]):
                b[key] = val

        if window is None:
            continue

        precip = window["details"].get("precipitation_amount")
        symbol = window["summary"].get("symbol_code")

        if precip is not None and (covered_until is None or t_local >= covered_until):
            b["precip"] += precip
            covered_until = t_local + timedelta(hours=span)

        # Periodesymbol vurderes på vinduets midtpunkt:
        # morgen 05–11, ettermiddag 11–17, kveld 17–23 (natt teller ikke).
        mid_hour = (t_local + timedelta(hours=span / 2)).hour
        idx = (
            0 if 5 <= mid_hour < 11
            else 1 if 11 <= mid_hour < 17
            else 2 if 17 <= mid_hour < 23
            else None
        )
        if idx is not None and symbol:
            base = re.sub(r"_(day|night|polartwilight)$", "", symbol)
            slot = b["_periods"][idx].setdefault(base, [0, symbol])
            slot[0] += 1

        b["hours"].append(
            {
                "hour": t_local.hour,
                "span": span,
                "temp": round(temp, 1) if temp is not None else None,
                "precip": round(precip, 1) if precip is not None else None,
                "wind": round(wind, 1) if wind is not None else None,
                "gust": round(gust, 1) if gust is not None else None,
                "uv": round(uv, 1) if uv is not None else None,
                "symbol": symbol,
            }
        )

    def _pick_symbol(counts: dict) -> str | None:
        """Vanligste symbol i perioden; ved likt antall det mest optimistiske."""
        if not counts:
            return None
        best = max(counts.values(), key=lambda s: (s[0], -_severity(s[1])))
        return best[1]

    out: list[dict] = []
    for day in sorted(days):
        b = days[day]
        b["symbols"] = [_pick_symbol(c) for c in b.pop("_periods")]
        b["precip"] = round(b["precip"], 1)
        for key in ("min_temp", "max_temp"):
            if b[key] is not None:
                b[key] = round(b[key], 1)
        for key in ("max_wind", "max_gust"):
            if b[key] is not None:
                b[key] = round(b[key], 1)
        if b["max_uv"] is not None:
            b["max_uv"] = round(b["max_uv"], 1)
        out.append(b)
    return out


def fetch_weather(lat: float, lon: float) -> dict:
    """
    Returnér værvarsling for én posisjon (MET Locationforecast) som dict:
      summary    — én kort linje (nåværende + ettermiddag)
      rain_hours — tidsspenn der nedbør >= 1 mm/t resten av dagen
      hourly     — timesserie for i dag (temp/nedbør/vind/kast/UV/symbol)
      daily      — 7 dagsvarsler (i dag + 6) fra _build_daily()
      fetched_at — HH:MM da varselet ble hentet (vises på nettsiden)
    """
    try:
        resp = httpx.get(
            _YR_URL.format(lat=lat, lon=lon),
            headers={"User-Agent": "news-briefing/1.0 (personal script)"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()

        ts = data["properties"]["timeseries"]
        now_data = ts[0]["data"]
        instant = now_data["instant"]["details"]

        temp = instant.get("air_temperature", "?")
        wind = instant.get("wind_speed", "?")

        symbol, now_precip = "?", 0.0
        for window in ("next_1_hours", "next_6_hours"):
            if window in now_data:
                symbol = _symbol_no(now_data[window]["summary"]["symbol_code"])
                now_precip = now_data[window]["details"].get(
                    "precipitation_amount", 0.0
                )
                break

        summary = f"{temp:.0f}°C, {symbol}, vind {wind:.0f} m/s"
        if now_precip > 0.2:
            summary += f", {now_precip:.1f} mm/t nå"

        # Ettermiddag (ca. kl. 15 lokal tid)
        now_local = datetime.now().astimezone()
        today_date = now_local.date()
        target_hour = 15
        for entry in ts[1:20]:
            t_local = datetime.fromisoformat(
                entry["time"].replace("Z", "+00:00")
            ).astimezone()
            if t_local.date() > today_date:
                break
            if t_local.hour >= target_hour:
                aft = entry["data"]
                for window in ("next_1_hours", "next_6_hours"):
                    if window in aft:
                        aft_sym = _symbol_no(aft[window]["summary"]["symbol_code"])
                        aft_temp = aft["instant"]["details"].get("air_temperature", "?")
                        if aft_sym != symbol:
                            summary += f" → ettermiddag {aft_sym} {aft_temp:.0f}°C"
                        break
                break

        # Scan hele dagen: nedbør, klarvær-timer, UV, temperaturstatistikk
        rain_hours: list[str] = []
        sun_hours: list[int] = []
        max_uv = 0.0
        max_uv_hour: int | None = None
        max_temp_day: float | None = None
        max_temp_hour: int | None = None
        temp_0700: float | None = None
        hourly: list[dict] = []

        for entry in ts:
            t_local = datetime.fromisoformat(
                entry["time"].replace("Z", "+00:00")
            ).astimezone()
            if t_local.date() > today_date:
                break
            if t_local.date() < today_date:
                continue

            hour = t_local.hour
            d = entry["data"]
            inst = d["instant"]["details"]

            # Temp kl. 07:00
            if hour == 7:
                temp_0700 = inst.get("air_temperature")

            # Maks temperatur i dag
            t_val = inst.get("air_temperature")
            if t_val is not None:
                if max_temp_day is None or t_val > max_temp_day:
                    max_temp_day = t_val
                    max_temp_hour = hour

            # Maks UV-indeks (clear-sky)
            uv = inst.get("ultraviolet_index_clear_sky") or 0.0
            if uv > max_uv:
                max_uv = uv
                max_uv_hour = hour

            # Klarvær-timer (clearsky-symbol, dagstid)
            if "next_1_hours" in d and 5 <= hour <= 21:
                sym_code = d["next_1_hours"]["summary"]["symbol_code"]
                base = re.sub(r"_(day|night|polartwilight)$", "", sym_code)
                if base == "clearsky":
                    sun_hours.append(hour)

            # Nedbørstimer >= 1 mm/t resten av i dag
            if t_local >= now_local and "next_1_hours" in d:
                mm = d["next_1_hours"]["details"].get("precipitation_amount", 0.0)
                if mm >= 1.0:
                    rain_hours.append(f"{hour:02d}–{hour + 1:02d}")

            # Timesserie for værpanelet på nettsiden (per time i dag).
            # next_1_hours gir nedbør + symbol per time; instant gir temp/vind/UV.
            h_precip = h_symbol = None
            if "next_1_hours" in d:
                h_precip = round(
                    d["next_1_hours"]["details"].get("precipitation_amount", 0.0), 1
                )
                h_symbol = d["next_1_hours"]["summary"]["symbol_code"]
            h_wind = inst.get("wind_speed")
            h_gust = inst.get("wind_speed_of_gust")
            hourly.append(
                {
                    "hour": hour,
                    "temp": round(t_val, 1) if t_val is not None else None,
                    "precip": h_precip,
                    "wind": round(h_wind, 1) if h_wind is not None else None,
                    "gust": round(h_gust, 1) if h_gust is not None else None,
                    "uv": round(uv, 1),
                    "symbol": h_symbol,
                }
            )

        # Bygg klarvær-perioder (sammenhengende timer → intervall)
        sun_periods: list[str] = []
        if sun_hours:
            start = sun_hours[0]
            end = sun_hours[0]
            for h in sun_hours[1:]:
                if h == end + 1:
                    end = h
                else:
                    sun_periods.append(f"kl. {start:02d}00–{end + 1:02d}00")
                    start = h
                    end = h
            sun_periods.append(f"kl. {start:02d}00–{end + 1:02d}00")

        return {
            "summary": summary,
            "rain_hours": rain_hours,
            "sun_periods": sun_periods,
            "max_uv": round(max_uv) if max_uv else None,
            "max_uv_hour": max_uv_hour,
            "max_temp": max_temp_day,
            "max_temp_hour": max_temp_hour,
            "temp_0700": temp_0700,
            "hourly": hourly,
            "daily": _build_daily(ts, today_date),
            "fetched_at": now_local.strftime("%H:%M"),
        }

    except Exception as exc:
        return {
            "summary": f"utilgjengelig ({exc})",
            "rain_hours": [],
            "sun_periods": [],
            "max_uv": None,
            "max_uv_hour": None,
            "max_temp": None,
            "max_temp_hour": None,
            "temp_0700": None,
            "hourly": [],
            "daily": [],
            "fetched_at": None,
        }


def fetch_all_weather() -> tuple[dict, dict]:
    """Hent vær for alle WEATHER_LOCATIONS. Returnerer (bergen, weather_alt)
    der weather_alt kun inneholder steder som faktisk lyktes."""
    results: dict[str, dict] = {}
    for key, loc in WEATHER_LOCATIONS.items():
        results[key] = fetch_weather(loc["lat"], loc["lon"])
    bergen = results["bergen"]
    alt = {
        key: w
        for key, w in results.items()
        if key != "bergen" and not str(w.get("summary", "")).startswith("utilgjengelig")
    }
    return bergen, alt


# ─────────────────────────────────────────────────────────────────────────────
# Markedssnapshot (yfinance / Yahoo Finance)
# ─────────────────────────────────────────────────────────────────────────────


def fetch_market_snapshot() -> dict:
    """
    Returnér markedsdata som dict. Myk feil — stopper ikke kjøringen.
    Keys: brent, brent_chg, sp500, sp500_chg, osebx, osebx_chg, btc, btc_chg,
          eth, eth_chg, nordnet, nordnet_chg, error
    nordnet = Nordnet Global (MSCI World-proxy via URTH; bytt ticker for en annen indeks).
    """
    try:
        import logging
        import yfinance as yf

        logging.getLogger("yfinance").setLevel(logging.ERROR)

        # URTH = iShares MSCI World (USD) — proxy for «Nordnet Global». Bytt ticker her
        # (og i terminal/Notion-utskriften under) for en annen global indeks.
        t = yf.Tickers("BZ=F ^GSPC OBX.OL BTC-USD ETH-USD URTH")

        def _pct(info) -> tuple:
            last = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)
            chg = (last / prev - 1) * 100 if last and prev else None
            return last, chg

        brent, brent_chg = _pct(t.tickers["BZ=F"].fast_info)
        sp500, sp500_chg = _pct(t.tickers["^GSPC"].fast_info)
        osebx, osebx_chg = _pct(t.tickers["OBX.OL"].fast_info)
        btc, btc_chg = _pct(t.tickers["BTC-USD"].fast_info)
        eth, eth_chg = _pct(t.tickers["ETH-USD"].fast_info)
        nordnet, nordnet_chg = _pct(t.tickers["URTH"].fast_info)

        return {
            "brent": brent, "brent_chg": brent_chg,
            "sp500": sp500, "sp500_chg": sp500_chg,
            "osebx": osebx, "osebx_chg": osebx_chg,
            "btc": btc, "btc_chg": btc_chg,
            "eth": eth, "eth_chg": eth_chg,
            "nordnet": nordnet, "nordnet_chg": nordnet_chg,
            "error": None,
        }
    except Exception as exc:
        return {
            "brent": None, "brent_chg": None,
            "sp500": None, "sp500_chg": None,
            "osebx": None, "osebx_chg": None,
            "btc": None, "btc_chg": None,
            "eth": None, "eth_chg": None,
            "nordnet": None, "nordnet_chg": None,
            "error": str(exc),
        }


def market_notion_blocks(market: dict) -> list[dict]:
    """Bygg Notion-blokker for markedssnapshot (plasseres mellom vær og nyheter)."""
    if market.get("error"):
        text = f"Markedsdata utilgjengelig: {market['error']}"
    else:
        def _idx(price, chg, decimals=0) -> str:
            p = f"{price:,.{decimals}f}".replace(",", " ") if price is not None else "–"
            c = f"{chg:+.1f} %" if chg is not None else ""
            return f"{c} ({p})".strip() if c else p

        line1 = (
            f"Brent {_idx(market['brent'], market['brent_chg'], 1)} $"
            f"  ·  S&P 500 {_idx(market['sp500'], market['sp500_chg'])}"
            f"  ·  OBX {_idx(market['osebx'], market['osebx_chg'])}"
        )
        line2 = (
            f"BTC {_idx(market.get('btc'), market.get('btc_chg'))} $"
            f"  ·  ETH {_idx(market.get('eth'), market.get('eth_chg'))} $"
            f"  ·  Nordnet Global {_idx(market.get('nordnet'), market.get('nordnet_chg'), 1)}"
        )
        text = line1 + "\n" + line2

    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": text}}]
            },
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Dagens quiz (lokalt spørsmålsbibliotek — norsk, ingen ekstern API/Claude)
# ─────────────────────────────────────────────────────────────────────────────
#
# Spørsmålene bor i quiz_bank/<kategori>.json (i repoet, følger med i imaget).
# Én fil = én kategori; hver dag trekkes ett nytt spørsmål per kategorifil, så
# antall spørsmål/dag = antall filer. Legg til flere kategorifiler → flere
# spørsmål/dag, uten kodeendring. Rekkefølgen på kategoriene i quizen styres av
# _QUIZ_CATEGORY_ORDER (kjente filer først, resten alfabetisk).

_QUIZ_BANK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quiz_bank")
_QUIZ_SEEN_FILE = "quiz_seen.json"  # i BRIEFING_DATA_DIR — må persisteres (volumet)
_QUIZ_SEEN_RETENTION_DAYS = 365
# Foretrukket rekkefølge (filnavn uten .json). Ukjente filer legges bakerst
# alfabetisk, så nye kategorier virker uten å endre denne lista.
_QUIZ_CATEGORY_ORDER = [
    "historie",
    "geografi",
    "norsk_samfunn",
    "medisin_og_kropp",
]
# Vanskelighetsgrad roterer per dag/kategori så en bruker møter en blanding
# over uka i stedet for samme nivå hver gang.
_QUIZ_DIFFICULTY_CYCLE = ["easy", "medium", "hard"]
# Spaced repetition: i tillegg til dagens ferske spørsmål hentes ett tidligere
# sett spørsmål tilbake når det er «forfalt» for repetisjon (retrieval practice
# + spacing er den best dokumenterte lærings­kombinasjonen). Intervallet vokser
# med antall ganger spørsmålet er vist (utvidende repetisjon, Leitner-aktig):
# indeks = reps-1, klemt til siste. Repetisjonsspørsmålet merkes `repeat: True`
# så nettsiden kan vise et repetisjonsmerke.
_QUIZ_REVIEW_INTERVALS = [7, 30, 90, 180]  # dager før neste repetisjon


def _quiz_seen_path() -> str:
    return os.path.join(os.environ.get("BRIEFING_DATA_DIR", "."), _QUIZ_SEEN_FILE)


def _load_quiz_seen() -> dict:
    """{normalisert spørsmål: {"last": 'YYYY-MM-DD', "reps": int}}, prunet.

    Bakoverkompatibel med det gamle formatet der verdien var en ren datostreng
    (= spørsmålet vist én gang). Prunes etter retention på sist-sett-datoen.
    """
    try:
        with open(_quiz_seen_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cutoff = (datetime.now() - timedelta(days=_QUIZ_SEEN_RETENTION_DAYS)).strftime(
        "%Y-%m-%d"
    )
    seen: dict = {}
    for k, v in raw.items():
        if isinstance(v, str):
            rec = {"last": v, "reps": 1}
        elif isinstance(v, dict) and isinstance(v.get("last"), str):
            rec = {"last": v["last"], "reps": int(v.get("reps", 1) or 1)}
        else:
            continue
        if rec["last"] >= cutoff:
            seen[k] = rec
    return seen


def _save_quiz_seen(seen: dict) -> None:
    path = _quiz_seen_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_quiz_bank() -> list[tuple[str, dict]]:
    """
    Les alle kategorifiler i quiz_bank/, sortert etter _QUIZ_CATEGORY_ORDER
    (kjente først, resten alfabetisk). Returnerer [(slug, data)] der data er
    { "category": <visningsnavn>, "questions": [ { difficulty, question,
    answer, options } ] }. Myk feil per fil.
    """
    try:
        files = [f for f in os.listdir(_QUIZ_BANK_DIR) if f.endswith(".json")]
    except OSError:
        return []

    def sort_key(fname: str):
        slug = fname[:-5]
        try:
            return (0, _QUIZ_CATEGORY_ORDER.index(slug), "")
        except ValueError:
            return (1, 0, slug)

    banks: list[tuple[str, dict]] = []
    for fname in sorted(files, key=sort_key):
        path = os.path.join(_QUIZ_BANK_DIR, fname)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ⚠  quiz: kunne ikke lese {fname} — {exc}")
            continue
        if isinstance(data.get("questions"), list) and data["questions"]:
            banks.append((fname[:-5], data))
    return banks


def _pick_review_question(seen: dict, bank_map: dict, day_ord: int):
    """
    Velg ett tidligere sett spørsmål som er forfalt for repetisjon (spaced
    repetition). Et spørsmål vist `reps` ganger er forfalt når alderen (dager
    siden sist sett) ≥ _QUIZ_REVIEW_INTERVALS[reps-1] (klemt til siste). Blant
    forfalte velges det mest forfalte (størst overskridelse); uavgjort brytes
    deterministisk av innsettingsrekkefølgen. Spørsmål som ikke lenger finnes i
    banken ignoreres.

    `bank_map`: {normalisert spørsmål: (slug, label, question_dict)}.
    Returnerer (norm_key, question_dict, label) eller None.
    """
    best = None
    best_overdue = -1
    for key, rec in seen.items():
        entry = bank_map.get(key)
        if entry is None:
            continue
        reps = max(1, rec.get("reps", 1))
        interval = _QUIZ_REVIEW_INTERVALS[min(reps, len(_QUIZ_REVIEW_INTERVALS)) - 1]
        try:
            last_ord = datetime.strptime(rec["last"], "%Y-%m-%d").toordinal()
        except (ValueError, KeyError, TypeError):
            continue
        overdue = (day_ord - last_ord) - interval
        if overdue >= 0 and overdue > best_overdue:
            best_overdue = overdue
            best = (key, entry)
    if best is None:
        return None
    key, (_slug, label, q) = best
    return key, q, label


def fetch_daily_quiz() -> list[dict]:
    """
    Trekk ett nytt spørsmål per kategori fra det lokale spørsmålsbiblioteket
    (quiz_bank/). Dedup mot quiz_seen.json så samme spørsmål ikke gjentas innen
    retention-vinduet. Vanskelighetsgraden roterer per dag/kategori for en
    blanding over uka; går tom for ferske spørsmål på ønsket nivå faller vi
    tilbake til andre nivåer, og til slutt til allerede sette (biblioteket kan
    være mindre enn retention-vinduet).

    I tillegg hentes ett tidligere sett spørsmål tilbake som repetisjon når det
    er forfalt (spaced repetition, se _pick_review_question). Dette merkes
    `repeat: True` og legges sist. Finnes ingen forfalte spørsmål (tidlige
    dager) utelates det.

    Returnerer liste av { level, difficulty, category, question, options,
    answer[, repeat] } der options er stokket og answer er fasitteksten.
    """
    import random

    banks = _load_quiz_bank()
    if not banks:
        print("  ⚠  quiz: fant ingen kategorifiler i quiz_bank/")
        return []

    seen = _load_quiz_seen()
    today = datetime.now().strftime("%Y-%m-%d")
    day_ord = datetime.now().toordinal()
    quiz: list[dict] = []
    drawn_keys: set[str] = set()

    # Oppslag normalisert spørsmål → (slug, label, question_dict) for å hente
    # tilbake fulle data til repetisjonsspørsmålet. Velg kandidaten fra
    # gårsdagens seen-tilstand (før dagens ferske spørsmål markeres) så dagens
    # nye spørsmål aldri kan bli valgt som repetisjon.
    bank_map: dict = {}
    for slug, data in banks:
        label = data.get("category", slug)
        for q in data["questions"]:
            qn = _norm_title(q.get("question", ""))
            if qn:
                bank_map.setdefault(qn, (slug, label, q))
    review = _pick_review_question(seen, bank_map, day_ord)

    for cat_i, (slug, data) in enumerate(banks):
        label = data.get("category", slug)
        questions = data["questions"]
        # Kandidater som ikke er brukt innen retention-vinduet.
        unseen = [q for q in questions if _norm_title(q.get("question", "")) not in seen]
        pool = unseen or questions  # tom bank → tillat gjenbruk

        # Ønsket nivå roterer per dag og kategori.
        want = _QUIZ_DIFFICULTY_CYCLE[(day_ord + cat_i) % len(_QUIZ_DIFFICULTY_CYCLE)]
        # Prøv ønsket nivå først, deretter resten i syklusrekkefølge.
        ordered_diffs = _QUIZ_DIFFICULTY_CYCLE[_QUIZ_DIFFICULTY_CYCLE.index(want):] + \
            _QUIZ_DIFFICULTY_CYCLE[:_QUIZ_DIFFICULTY_CYCLE.index(want)]
        chosen = None
        for diff in ordered_diffs:
            matches = [q for q in pool if q.get("difficulty") == diff]
            if matches:
                chosen = random.choice(matches)
                break
        if chosen is None:
            chosen = random.choice(pool)

        options = list(chosen.get("options", []))
        answer = chosen.get("answer", "")
        question = chosen.get("question", "")
        if not question or not answer or len(options) < 2:
            print(f"  ⚠  quiz: hoppet over ugyldig spørsmål i {slug}")
            continue
        random.shuffle(options)
        quiz.append(
            {
                "level": len(quiz) + 1,
                "difficulty": chosen.get("difficulty", ""),
                "category": label,
                "question": question,
                "options": options,
                "answer": answer,
            }
        )
        key = _norm_title(question)
        drawn_keys.add(key)
        prev = seen.get(key)
        reps = (prev.get("reps", 1) + 1) if isinstance(prev, dict) else 1
        seen[key] = {"last": today, "reps": reps}

    # Repetisjonsspørsmål sist — hopp over hvis det tilfeldigvis er trukket som
    # ferskt spørsmål i dag (kan skje i fallback-grenen når banken er liten).
    if review is not None:
        rkey, rq, rlabel = review
        options = list(rq.get("options", []))
        answer = rq.get("answer", "")
        question = rq.get("question", "")
        if rkey not in drawn_keys and question and answer and len(options) >= 2:
            random.shuffle(options)
            quiz.append(
                {
                    "level": len(quiz) + 1,
                    "difficulty": rq.get("difficulty", ""),
                    "category": rlabel,
                    "question": question,
                    "options": options,
                    "answer": answer,
                    "repeat": True,
                }
            )
            prev = seen.get(rkey)
            reps = (prev.get("reps", 1) + 1) if isinstance(prev, dict) else 2
            seen[rkey] = {"last": today, "reps": reps}

    if quiz:
        _save_quiz_seen(seen)
    return quiz


# ─────────────────────────────────────────────────────────────────────────────
# Dagens gåter (logikkgåter på norsk fra lokal gåtebank — riddle_bank/gaater.json;
# ingen Claude-bruk, ingen ekstern API)
# ─────────────────────────────────────────────────────────────────────────────

_RIDDLES_SEEN_FILE = "riddles_seen.json"  # i BRIEFING_DATA_DIR — må persisteres
_RIDDLES_SEEN_RETENTION_DAYS = 120

# Gåtene bor i riddle_bank/gaater.json (i repoet, følger med i imaget via COPY . .).
# Hver gåte har level (1–3), genre (må matche navnene i _RIDDLE_GENRES), question,
# answer og explanation. Ingen Claude-bruk — banken er håndlaget og fasit-verifisert.
_RIDDLE_BANK_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "riddle_bank", "gaater.json"
)

# Sjangre roteres deterministisk per dag (dag-ordinal) så variasjonen er garantert —
# dagens tre typer styrer hvilke gåter som trekkes fra banken, én per nivå.
_RIDDLE_GENRES: list[tuple[str, str]] = [
    ("sann/løgn-deduksjon", "noen snakker sant, andre lyver — finn ut hvem eller hva"),
    ("plasseringslogikk", "Einstein-aktig: personer, steder og egenskaper kobles ut fra ledetråder"),
    ("tallmønster", "finn neste tall eller det avvikende tallet i en rekke med skjult regel"),
    ("aldersalgebra", "aldre nå, før og senere som må settes opp som likninger og løses"),
    ("vekt og veiing", "finn den avvikende gjenstanden med færrest mulige veiinger o.l."),
    ("planlegging og kryssing", "elvekryssing, omhelling mellom kanner, broer med tidsfrister o.l."),
    ("informasjonsdeduksjon", "hatter/lapper: hva kan hver person slutte seg til ut fra det de ser og hører"),
    ("kalender- og tidslogikk", "ukedager, datoer og tidsintervaller med en skjult tvist"),
    ("lateral tenkning", "en tilsynelatende umulig situasjon med en logisk, ikke-språklig forklaring"),
    ("rekkefølge og sammenlikning", "hvem er eldst/raskest/høyest ut fra parvise sammenlikninger med tvist"),
]

def _todays_riddle_genres(today: datetime) -> list[tuple[str, str]]:
    """Tre sjangre for dagen (nivå 1–3), rotert deterministisk på dag-ordinal.

    Vindu på 3 som flyttes 3 plasser per dag; med 10 sjangre (gcd(3, 10) = 1)
    treffer vinduet alle startposisjoner over en 10-dagers syklus.
    """
    n = len(_RIDDLE_GENRES)
    start = (today.toordinal() * 3) % n
    return [_RIDDLE_GENRES[(start + i) % n] for i in range(3)]


def _riddles_seen_path() -> str:
    return os.path.join(os.environ.get("BRIEFING_DATA_DIR", "."), _RIDDLES_SEEN_FILE)


def _load_riddles_seen() -> dict:
    """{gåtetekst: 'YYYY-MM-DD' brukt}, prunet etter retention."""
    try:
        with open(_riddles_seen_path(), encoding="utf-8") as f:
            seen = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    cutoff = (datetime.now() - timedelta(days=_RIDDLES_SEEN_RETENTION_DAYS)).strftime(
        "%Y-%m-%d"
    )
    return {k: v for k, v in seen.items() if isinstance(v, str) and v >= cutoff}


def _save_riddles_seen(seen: dict) -> None:
    path = _riddles_seen_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_riddle_bank() -> list[dict]:
    """Les gåtebanken; hopper over ugyldige oppføringer. Myk feil → tom liste."""
    try:
        with open(_RIDDLE_BANK_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ✗  gåter: kunne ikke lese {_RIDDLE_BANK_FILE} — {exc}")
        return []
    bank: list[dict] = []
    for item in data.get("riddles", []):
        q = str(item.get("question", "")).strip()
        a = str(item.get("answer", "")).strip()
        level = item.get("level")
        if not q or not a or level not in (1, 2, 3):
            continue
        bank.append(
            {
                "level": level,
                "genre": str(item.get("genre", "")).strip(),
                "question": q,
                "answer": a,
                "explanation": str(item.get("explanation", "")).strip(),
            }
        )
    return bank


def fetch_daily_riddles() -> list[dict]:
    """
    Trekk 3 norske logikkgåter (nivå 1–3) fra den lokale gåtebanken
    (riddle_bank/gaater.json). Ingen Claude-bruk. Dagens sjanger per nivå styres av
    _todays_riddle_genres(); dedup mot riddles_seen.json sikrer at samme gåte aldri
    trekkes igjen så lenge nivået har usette gåter. Først når alle på et nivå er
    brukt (og eldre enn retention-vinduet ikke har frigjort noen), gjenbrukes den
    som ble vist for lengst siden. Myk feil → [].

    Returnerer liste av { level, question, answer, explanation }.
    """
    bank = _load_riddle_bank()
    if not bank:
        print("  ⚠  gåter: tom eller manglende gåtebank — utelates i dag")
        return []

    seen = _load_riddles_seen()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    genres = _todays_riddle_genres(now)

    riddles: list[dict] = []
    for level, (genre_name, _desc) in enumerate(genres, start=1):
        candidates = [r for r in bank if r["level"] == level]
        unseen = [r for r in candidates if r["question"] not in seen]
        in_genre = [r for r in unseen if r["genre"] == genre_name]
        if in_genre:
            pick = in_genre[0]
        elif unseen:
            pick = unseen[0]
        elif candidates:
            # Alt sett: gjenbruk den som ble vist for lengst siden (LRU).
            pick = min(candidates, key=lambda r: seen.get(r["question"], ""))
        else:
            print(f"  ⚠  gåter: ingen gåter på nivå {level} i banken")
            continue
        seen[pick["question"]] = today
        riddles.append(
            {
                "level": level,
                "question": pick["question"],
                "answer": pick["answer"],
                "explanation": pick["explanation"],
            }
        )

    if riddles:
        _save_riddles_seen(seen)
    return riddles


# ─────────────────────────────────────────────────────────────────────────────
# Dagens læring (podcast-råd + boktips — Claude kuraterer fra podcast-RSS)
# ─────────────────────────────────────────────────────────────────────────────

PODCAST_FEEDS: dict[str, str] = {
    "Lenny's Podcast": "https://api.substack.com/feed/podcast/10845.rss",
    "Huberman Lab": "https://feeds.megaphone.fm/hubermanlab",
    "The Tim Ferriss Show": "https://rss.art19.com/tim-ferriss-show",
    "Dwarkesh Podcast": "https://api.substack.com/feed/podcast/69345.rss",
    "The Diary Of A CEO": "https://feeds.megaphone.fm/thediaryofaceo",
}

_LEARNING_SEEN_FILE = "learning_seen.json"  # i BRIEFING_DATA_DIR — må persisteres
_LEARNING_SEEN_RETENTION_DAYS = 180
_LEARNING_LOOKBACK_DAYS = 14
_LEARNING_MAX_PER_FEED = 4
_LEARNING_DESC_CHARS = 500
_LEARNING_MAX_TOKENS = 2000

_LEARNING_SYSTEM_PROMPT = """Du kuraterer daglig læring på norsk for en investor i Bergen \
som er interessert i AI, produktutvikling, investering, helse og selvutvikling.

Du får en nummerert liste med ferske podcast-episoder (tittel + beskrivelse). Oppgaven:

1. PODCAST-RÅD: Velg de 1–2 episodene med mest konkret, anvendbar innsikt for brukeren.
   For hver: skriv «tip» — selve rådet/innsikten fra episoden i 1–2 setninger på norsk.
   Rådet skal stå på egne ben (leseren skal lære noe uten å høre episoden), utledet av
   tittel og beskrivelse. Ikke skriv «i denne episoden …» — gi selve rådet.
   Referer episoden KUN med «id» fra listen. Er ingen episoder gode nok, returner færre
   eller tom liste.

2. BOKTIPS: Anbefal 1–2 bøker for læring — selvutvikling, fakta/sakprosa eller tenkning.
   Foretrekk nyere, aktuelle bøker; tidløse moderne klassikere er OK som én av to.
   «why» = 1–2 setninger på norsk om hvorfor akkurat denne, knyttet til brukerens
   interesser. Ikke anbefal bøker fra unngå-listen.

SVAR KUN med gyldig JSON, ingen tekst utenfor, ingen markdown-fences:
{"podcasts": [{"id": 3, "tip": "…"}], "books": [{"title": "…", "author": "…", "year": 2025, "why": "…"}]}"""


def _learning_seen_path() -> str:
    return os.path.join(os.environ.get("BRIEFING_DATA_DIR", "."), _LEARNING_SEEN_FILE)


def _load_learning_seen() -> dict:
    """{"episodes": {norm tittel: dato}, "books": {norm tittel: dato}}, prunet."""
    try:
        with open(_learning_seen_path(), encoding="utf-8") as f:
            seen = json.load(f)
    except (OSError, json.JSONDecodeError):
        seen = {}
    cutoff = (datetime.now() - timedelta(days=_LEARNING_SEEN_RETENTION_DAYS)).strftime(
        "%Y-%m-%d"
    )
    return {
        part: {
            k: v
            for k, v in (seen.get(part) or {}).items()
            if isinstance(v, str) and v >= cutoff
        }
        for part in ("episodes", "books")
    }


def _save_learning_seen(seen: dict) -> None:
    path = _learning_seen_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _fetch_podcast_episodes(seen_episodes: dict) -> list[dict]:
    """Ferske episoder fra PODCAST_FEEDS: [{ podcast, title, url, date, description }].
    Myk feil per feed. Episoder som allerede er brukt (seen) hoppes over."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_LEARNING_LOOKBACK_DAYS)
    episodes: list[dict] = []
    for podcast, url in PODCAST_FEEDS.items():
        try:
            resp = httpx.get(url, headers=_FETCH_HEADERS, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            count = 0
            for entry in feed.entries:
                if count >= _LEARNING_MAX_PER_FEED:
                    break
                raw = getattr(entry, "published_parsed", None) or getattr(
                    entry, "updated_parsed", None
                )
                if not raw:
                    continue
                try:
                    published = datetime.fromtimestamp(time.mktime(raw), tz=timezone.utc)
                except (OverflowError, ValueError, OSError):
                    continue
                if published < cutoff:
                    continue
                title = entry.get("title", "").strip()
                if not title or _norm_title(title) in seen_episodes:
                    continue
                episodes.append(
                    {
                        "podcast": podcast,
                        "title": title,
                        "url": entry.get("link", ""),
                        "date": published.strftime("%Y-%m-%d"),
                        "description": _strip_html(
                            entry.get("summary", entry.get("description", ""))
                        )[:_LEARNING_DESC_CHARS],
                    }
                )
                count += 1
            print(f"  ✓  {podcast}: {count} episoder" if count else f"  –  {podcast}: ingen nye episoder")
        except Exception as exc:
            print(f"  ✗  {podcast}: feil ved henting — {exc}")
    return episodes


def fetch_daily_learning() -> dict | None:
    """
    Kuratér dagens læring: 1–2 podcast-råd (fra ferske episoder i PODCAST_FEEDS)
    + 1–2 boktips, valgt av Claude i ett lite kall. Myk feil — returnerer None.

    Returnerer {"podcasts": [{ podcast, episode, url, date, tip }],
                "books": [{ title, author, year, why }]}.
    Episode-referanser skjer via id mot vår egen liste, så podcast/tittel/URL
    aldri kan hallusineres — kun rådsteksten kommer fra Claude.
    """
    seen = _load_learning_seen()
    today = datetime.now().strftime("%Y-%m-%d")

    episodes = _fetch_podcast_episodes(seen["episodes"])

    ep_lines = [
        f"#{i} [{e['podcast']}] {e['title']} ({e['date']})\n{e['description']}"
        for i, e in enumerate(episodes)
    ]
    avoid_books = sorted(seen["books"], key=seen["books"].get, reverse=True)
    user_content = (
        f"Dato: {today}\n\nFerske podcast-episoder:\n\n"
        + ("\n---\n".join(ep_lines) if ep_lines else "(ingen nye episoder i dag)")
        + (
            "\n\nUNNGÅ disse bøkene (allerede anbefalt):\n"
            + "\n".join(f"- {b}" for b in avoid_books)
            if avoid_books
            else ""
        )
    )

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=_LEARNING_MAX_TOKENS,
            system=_LEARNING_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        text = resp.content[0].text
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("fant ingen JSON i svaret")
        parsed = json.loads(text[start : end + 1])

        podcasts: list[dict] = []
        for p in (parsed.get("podcasts") or [])[:2]:
            try:
                idx = int(p["id"])
            except (KeyError, ValueError, TypeError):
                continue
            if not 0 <= idx < len(episodes):
                continue
            e = episodes[idx]
            tip = str(p.get("tip", "")).strip()
            if not tip:
                continue
            podcasts.append(
                {
                    "podcast": e["podcast"],
                    "episode": e["title"],
                    "url": e["url"],
                    "date": e["date"],
                    "tip": tip,
                }
            )
            seen["episodes"][_norm_title(e["title"])] = today

        books: list[dict] = []
        for b in (parsed.get("books") or [])[:2]:
            title = str(b.get("title", "")).strip()
            if not title:
                continue
            norm = _norm_title(title)
            if norm in seen["books"]:
                # Hard kontroll: Claude overså UNNGÅ-lista — hopp over duplikat.
                print(f"  –  boktips «{title}» allerede anbefalt før, hoppes over")
                continue
            books.append(
                {
                    "title": title,
                    "author": str(b.get("author", "")).strip(),
                    "year": b.get("year"),
                    "why": str(b.get("why", "")).strip(),
                }
            )
            seen["books"][norm] = today

        if not podcasts and not books:
            return None
        _save_learning_seen(seen)
        return {"podcasts": podcasts, "books": books}
    except Exception as exc:
        print(f"  ✗  læring: feil ved kuratering — {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Dagens refleksjon (Claude — 2 åpne refleksjonsspørsmål forankret i dagens
# nyheter og inspirasjon; elaborering/refleksjon er godt dokumentert for læring)
# ─────────────────────────────────────────────────────────────────────────────

_REFLECTION_MAX_TOKENS = 700

_REFLECTION_SYSTEM_PROMPT = """Du lager daglige refleksjonsspørsmål på norsk for en \
investor i Bergen som vil lære og vokse som person. Målet er elaborering: at leseren \
knytter dagens innhold til eget liv, tenkning eller handling.

KRAV:
- Lag inntil to spørsmål: ETT forankret i en konkret sak/tema fra dagens nyheter \
(focus "nyheter") og ETT forankret i dagens inspirasjon — et konkret podcast-råd eller \
boktips (focus "inspirasjon"). Mangler en av kildene, lag kun spørsmålet for den som finnes.
- Åpne spørsmål UTEN fasit. Aldri ja/nei, aldri faktaspørsmål. Personlig og handlingsrettet \
(«Hva ville du…», «Hvordan påvirker dette…», «Hva kan du gjøre annerledes…»).
- Forankre i det konkrete innholdet (nevn saken/rådet kort), men hold spørsmålet om leseren.
- Maks to setninger per spørsmål. Naturlig, ikke svulstig norsk.

SVAR KUN med en gyldig JSON-array, ingen tekst utenfor, ingen markdown-fences:
[{"focus": "nyheter", "prompt": "…"}, {"focus": "inspirasjon", "prompt": "…"}]"""


def _learning_prompt_context(learning: dict | None) -> str:
    """Kompakt tekst om dagens inspirasjon til refleksjonsprompten (eller '')."""
    if not learning:
        return ""
    lines: list[str] = []
    for p in learning.get("podcasts", []):
        lines.append(f"Podcast-råd ({p.get('podcast', '')}): {p.get('tip', '')}")
    for b in learning.get("books", []):
        author = f" av {b['author']}" if b.get("author") else ""
        lines.append(f"Boktips: «{b.get('title', '')}»{author} — {b.get('why', '')}")
    return "\n".join(lines)


def fetch_daily_reflection(news_md: str, learning: dict | None) -> list[dict]:
    """
    Generer inntil to åpne refleksjonsspørsmål med Claude: ett forankret i dagens
    nyheter, ett i dagens inspirasjon. Myk feil — returnerer [] ved API-/parsefeil.

    Returnerer liste av { focus, prompt } (focus ∈ {"nyheter", "inspirasjon"}).
    """
    if not news_md and not learning:
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    insp = _learning_prompt_context(learning)
    user_content = (
        f"Dato: {today}\n\nDAGENS NYHETER (markdown):\n\n{news_md or '(ingen nyheter i dag)'}"
        + (
            f"\n\nDAGENS INSPIRASJON:\n{insp}"
            if insp
            else "\n\nDAGENS INSPIRASJON: (ingen i dag — lag kun nyhets-spørsmålet)"
        )
    )

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=_REFLECTION_MAX_TOKENS,
            system=_REFLECTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        text = resp.content[0].text if resp.content else ""
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            raise ValueError("fant ingen JSON-array i svaret")
        items = json.loads(text[start : end + 1])
    except Exception as exc:
        print(f"  ✗  refleksjon: feil ved generering — {exc}")
        return []

    out: list[dict] = []
    seen_focus: set[str] = set()
    for item in items:
        focus = str(item.get("focus", "")).strip().lower()
        prompt = str(item.get("prompt", "")).strip()
        if focus not in ("nyheter", "inspirasjon") or not prompt:
            continue
        if focus in seen_focus:  # maks ett per kilde
            continue
        seen_focus.add(focus)
        out.append({"focus": focus, "prompt": prompt})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# SK Brann (NIFS API — åpen, ingen nøkkel; nyheter via Google News RSS)
# ─────────────────────────────────────────────────────────────────────────────

_NIFS_BASE = "https://api.nifs.no"
_BRANN_TEAM_ID = 1  # SK Brann herrer i NIFS
_ELITESERIEN_TOURNAMENT_ID = 5
_BRANN_NEWS_RSS = (
    "https://news.google.com/rss/search?q=%22SK+Brann%22&hl=no&gl=NO&ceid=NO:no"
)
_BRANN_NEWS_MAX = 1


def _nifs_get(path: str):
    resp = httpx.get(
        f"{_NIFS_BASE}/{path}",
        headers={"User-Agent": "news-briefing/1.0 (personal script)"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _brann_match_dict(m: dict) -> dict:
    """Kompakt kampdict sett fra Branns side (NIFS-kampobjekt inn)."""
    home = m["homeTeam"]["name"] == "Brann"
    opponent = m["awayTeam"]["name"] if home else m["homeTeam"]["name"]
    out = {
        "opponent": opponent,
        "home": home,
        "date": m.get("timestamp"),
        "round": m.get("round"),
        "stadium": (m.get("stadium") or {}).get("name"),
    }
    hs = (m.get("result") or {}).get("homeScore90")
    as_ = (m.get("result") or {}).get("awayScore90")
    if hs is not None and as_ is not None:
        brann_goals, opp_goals = (hs, as_) if home else (as_, hs)
        out["brann_goals"] = brann_goals
        out["opponent_goals"] = opp_goals
        out["outcome"] = (
            "seier" if brann_goals > opp_goals
            else "tap" if brann_goals < opp_goals
            else "uavgjort"
        )
    return out


def fetch_brann_info() -> dict | None:
    """
    SK Brann-status fra NIFS: tabellplassering, siste resultat og neste kamp i
    Eliteserien, pluss siste nyheter (skader/overganger o.l.) fra Google News.
    Myk feil per del; returnerer None kun hvis alt feiler.
    """
    info: dict = {"team": "SK Brann"}

    try:
        stages = _nifs_get(f"tournaments/{_ELITESERIEN_TOURNAMENT_ID}/stages/")
        year = datetime.now().year
        stage = next((s for s in stages if s.get("yearStart") == year), stages[0])
        info["season"] = stage.get("fullName")

        matches = _nifs_get(f"stages/{stage['id']}/matches/?teamId={_BRANN_TEAM_ID}")
        matches.sort(key=lambda m: m.get("timestamp") or "")
        played = [
            m for m in matches
            if (m.get("result") or {}).get("homeScore90") is not None
        ]
        def _match_time(m):
            try:
                return datetime.fromisoformat(m.get("timestamp") or "")
            except ValueError:
                return None

        now = datetime.now().astimezone()
        upcoming = [
            m for m in matches
            if (m.get("result") or {}).get("homeScore90") is None
            and (t := _match_time(m)) is not None and t >= now
        ]
        if played:
            info["last_match"] = _brann_match_dict(played[-1])
        if upcoming:
            info["next_match"] = _brann_match_dict(upcoming[0])

        table = _nifs_get(f"stages/{stage['id']}/table/")
        teams = table.get("teams") or []
        entry = next((t for t in teams if t.get("name") == "Brann"), None)
        if entry:
            info["table"] = {
                "place": entry.get("place"),
                "played": entry.get("played"),
                "won": entry.get("won"),
                "draw": entry.get("draw"),
                "lost": entry.get("lost"),
                "points": entry.get("points"),
                "teams": len(teams),
            }
    except Exception as exc:
        print(f"  ✗  Brann: feil mot NIFS — {exc}")

    try:
        resp = httpx.get(_BRANN_NEWS_RSS, headers=_FETCH_HEADERS, timeout=15,
                         follow_redirects=True)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        news = []
        for entry in feed.entries[:_BRANN_NEWS_MAX]:
            title = entry.get("title", "").strip()
            if not title:
                continue
            # Google News-titler slutter på « - Kilde» — løft kilden ut for seg
            head, sep, src = title.rpartition(" - ")
            item = {"title": head if sep else title, "url": entry.get("link", "")}
            if sep and src:
                item["source"] = src
            raw = getattr(entry, "published_parsed", None)
            if raw:
                item["published"] = time.strftime("%Y-%m-%d", raw)
            news.append(item)
        if news:
            info["news"] = news
    except Exception as exc:
        print(f"  ✗  Brann: feil ved nyhetshenting — {exc}")

    # Kun team-navn (og ev. season) → alt feilet
    if not any(k in info for k in ("table", "last_match", "next_match", "news")):
        return None
    return info


# ─────────────────────────────────────────────────────────────────────────────
# RSS-henting
# ─────────────────────────────────────────────────────────────────────────────


def _norm_title(title: str) -> str:
    """Normaliser tittel for dedup: små bokstaver, uten tegnsetting, kollapset luft."""
    t = re.sub(r"[^\w\s]", " ", title.lower())
    return re.sub(r"\s+", " ", t).strip()


# Lokalt kutt-filter — systemprompten sier «KUTT ALLTID: sport, kjendis, krim,
# underholdning, lokale ulykker», men artiklene ble likevel sendt til Claude, som
# betalte input-tokens for å forkaste dem. Dette filteret kutter de åpenbare
# tilfellene gratis FØR de teller mot MAX_PER_FEED, så plassene går til saker
# Claude faktisk kan bruke.
#
# Kun TITTELEN sjekkes, og listen er bevisst konservativ: en sak som feilkuttes
# her kan Claude aldri redde, mens en som slipper gjennom bare koster noen
# input-tokens. Derfor f.eks. «drapssiktet»/«siktet for», men aldri bare «drept»
# (som ville kuttet krigsnyheter under 🌍 Internasjonalt).
_CUT_TITLE_PATTERNS = [
    # Sport
    r"fotball", r"\blandslag", r"eliteserien", r"obos-ligaen", r"toppserien",
    r"champions league", r"europa league", r"premier league", r"bundesliga",
    r"la liga", r"\bserie a\b", r"håndball", r"skiskyting", r"langrenn",
    r"\balpint\b", r"hoppuka", r"friidrett", r"sykkelritt", r"tour de france",
    r"\bwimbledon\b", r"\btennis\b", r"\bnba\b", r"\bnhl\b", r"\bnfl\b",
    # (?<!')…(?!') unngår engelske sammentrekninger som «catch 'em all» / «good ol'»
    r"formel 1", r"(?<!')\b(?:vm|em|ol)\b(?!')", r"\bfootball\b", r"\bsoccer\b",
    r"\bcricket\b", r"\brugby\b", r"\bolympic", r"world cup", r"grand slam",
    # Krim (konservativt — krig/geopolitikk skal IKKE treffes)
    r"drapssiktet", r"drapstiltalt", r"drapsdømt", r"siktet for", r"tiltalt for",
    r"pågrepet", r"varetektsfengsl", r"knivstukket", r"knivstikking",
    r"voldtekt", r"\branet\b", r"væpnet ran", r"\binnbrudd\b", r"\bovergrep\b",
    # NB: ikke «sentenced to»/«found guilty» — treffer krigsforbryterdommer (🌍) og
    # store svindeldommer med markedseffekt (📈).
    r"\bmurder\b", r"\bstabbed\b", r"\bstabbing\b", r"\bjailed\b",
    r"\bmanslaughter\b", r"\brape\b",
    # Kjendis og underholdning
    r"\bkjendis", r"\beurovision\b", r"melodi grand prix", r"\bgrammy\b",
    r"\boscars\b", r"realityserie", r"realitydeltaker", r"\binfluenser\b",
    r"\bhollywood\b",
    # Lokale ulykker
    r"trafikkulykke", r"dødsulykke", r"frontkollisjon", r"kjørte av veien",
    r"mc-ulykke", r"\bpåkjørt\b",
]
_CUT_TITLE_RE = re.compile("|".join(_CUT_TITLE_PATTERNS), re.IGNORECASE)


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _load_recent_briefing_points(days: int = NEWS_HISTORY_DAYS) -> dict:
    """Hent punktene fra de siste dagenes nyhetsbriefinger i datalageret.

    Returnerer {"urls", "titles", "points"}: URL-er og normaliserte lenketitler
    filtrerer artikler mekanisk før Claude (sparer tokens), punkttekstene sendes
    som unngå-liste i prompten (fanger samme sak med ny overskrift). Ingen egen
    state-fil — briefingene er fasiten på hva leseren faktisk har fått servert.
    """
    data_dir = os.environ.get("BRIEFING_DATA_DIR", ".")
    urls: set[str] = set()
    titles: set[str] = set()
    points: list[str] = []
    for delta in range(1, days + 1):
        date_str = (datetime.now() - timedelta(days=delta)).strftime("%Y-%m-%d")
        path = os.path.join(data_dir, "briefings", f"{date_str}.json")
        try:
            with open(path, encoding="utf-8") as f:
                news_md = json.load(f).get("news_md") or ""
        except (OSError, json.JSONDecodeError):
            continue
        for line in news_md.splitlines():
            text = line.strip().lstrip("•-* ").strip()
            if not line.strip().startswith(("•", "- ", "* ")) or not text:
                continue
            if text.lower().startswith("ingen viktige hendelser"):
                continue
            for m in _MD_LINK_RE.finditer(text):
                titles.add(_norm_title(m.group(1)))
                urls.add(m.group(2).split("?")[0].rstrip("/"))
            points.append(_MD_LINK_RE.sub(r"\1", text))
    return {"urls": urls, "titles": titles, "points": points}


def _dedup_articles(articles: list[dict]) -> list[dict]:
    """Slå sammen nær-identiske saker som dukker opp i flere feeds (sparer Claude-tokens
    og fjerner støy). Dedupliserer på normalisert tittel og på URL; ved tittelduplikat
    beholdes artikkelen med lengst ingress (mest kontekst til Claude)."""
    out: list[dict] = []
    title_idx: dict[str, int] = {}
    seen_urls: set[str] = set()
    for a in articles:
        url = (a.get("url") or "").split("?")[0].rstrip("/")
        if url and url in seen_urls:
            continue
        key = _norm_title(a["title"])
        if key and key in title_idx:
            j = title_idx[key]
            if len(a["description"]) > len(out[j]["description"]):
                out[j] = a  # behold den rikeste varianten
            continue
        out.append(a)
        if key:
            title_idx[key] = len(out) - 1
        if url:
            seen_urls.add(url)
    return out


def fetch_articles(skip: dict | None = None) -> list[dict]:
    """Hent artikler fra alle feeds. `skip` (fra `_load_recent_briefing_points()`)
    filtrerer bort saker som allerede sto i tidligere briefinger, og
    `_CUT_TITLE_RE` kutter kategorier Claude uansett ville forkastet (sport, krim,
    kjendis, ulykker) — begge før artiklene teller mot MAX_PER_FEED, så plassene
    går til ferske, brukbare saker."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    articles: list[dict] = []
    skipped_seen = 0
    skipped_cut = 0

    for source, url in RSS_FEEDS.items():
        try:
            resp = httpx.get(url, headers=_FETCH_HEADERS, timeout=10, follow_redirects=True)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if feed.bozo and not feed.entries:
                print(f"  ⚠  {source}: kunne ikke hente feed ({url})")
                continue

            count = 0
            for entry in feed.entries:
                if count >= MAX_PER_FEED:
                    break

                # Forsøk å hente publiseringsdato
                published_dt = None
                for field in ("published_parsed", "updated_parsed"):
                    raw = getattr(entry, field, None)
                    if raw:
                        try:
                            published_dt = datetime.fromtimestamp(
                                time.mktime(raw), tz=timezone.utc
                            )
                        except (OverflowError, ValueError, OSError):
                            pass
                        break

                # Hopp over artikler som er eldre enn grensen (men inkluder de uten dato)
                if published_dt is not None and published_dt < cutoff:
                    continue

                title = entry.get("title", "").strip()
                description = entry.get(
                    "summary", entry.get("description", "")
                ).strip()[:MAX_DESC_CHARS]
                link = entry.get("link", "")

                if not title:
                    continue

                if _CUT_TITLE_RE.search(title):
                    skipped_cut += 1
                    continue

                if skip and (
                    link.split("?")[0].rstrip("/") in skip["urls"]
                    or _norm_title(title) in skip["titles"]
                ):
                    skipped_seen += 1
                    continue

                articles.append(
                    {
                        "source": source,
                        "title": title,
                        "description": description,
                        "url": link,
                        "published": (
                            published_dt.strftime("%H:%M") if published_dt else "–"
                        ),
                    }
                )
                count += 1

            if count:
                print(f"  ✓  {source}: {count} artikler")
            else:
                print(f"  –  {source}: ingen nye artikler siste {LOOKBACK_HOURS}t")

        except Exception as exc:
            print(f"  ✗  {source}: feil ved henting — {exc}")

    if skipped_cut:
        print(f"  ⓘ  {skipped_cut} artikler kuttet lokalt (sport/krim/kjendis/ulykker)")
    if skipped_seen:
        print(f"  ⓘ  {skipped_seen} artikler hoppet over (dekket i tidligere briefinger)")
    before = len(articles)
    articles = _dedup_articles(articles)
    removed = before - len(articles)
    if removed:
        print(f"  ⓘ  dedup: {before} → {len(articles)} artikler ({removed} duplikater fjernet)")
    return articles


# ─────────────────────────────────────────────────────────────────────────────
# Claude-oppsummering
# ─────────────────────────────────────────────────────────────────────────────


def build_articles_text(articles: list[dict]) -> str:
    lines = []
    for a in articles:
        lines.append(
            f"[{a['source']}] ({a['published']}) {a['title']}\n"
            f"URL: {a['url']}\n"
            f"{a['description']}\n"
            "---"
        )
    return "\n".join(lines)


def summarize_with_claude(articles: list[dict], prev_points: list[str] | None = None) -> str:
    client = anthropic.Anthropic()  # les ANTHROPIC_API_KEY automatisk fra env

    today_str = datetime.now().strftime("%A %d. %B %Y")
    articles_text = build_articles_text(articles)

    user_content = (
        f"Dato: {today_str}\n\n"
        f"Totalt {len(articles)} artikler fra siste {LOOKBACK_HOURS} timer:\n\n"
        f"{articles_text}"
    )
    if prev_points:
        user_content += (
            f"\n\nDEKKET I BRIEFINGENE DE SISTE DAGENE "
            "(ikke gjenta; kun ved vesentlig ny utvikling — fokuser da på det nye):\n"
            + "\n".join(f"- {p}" for p in prev_points)
        )

    print("\nOppsummerer med Claude (streamer svar)...\n")
    print("─" * 70)

    collected_text = ""
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        for chunk in stream.text_stream:
            print(chunk, end="", flush=True)
            collected_text += chunk

    print()  # linjeskift etter streaming
    return collected_text


# ─────────────────────────────────────────────────────────────────────────────
# Notion-publisering
# ─────────────────────────────────────────────────────────────────────────────


def parse_inline_links(text: str) -> list[dict]:
    """Konverter [tittel](url) markdown-lenker til Notion rich_text-elementer."""
    NOTION_TEXT_LIMIT = 1990  # Notion har 2000-tegns grense
    parts: list[dict] = []
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    last = 0

    for m in pattern.finditer(text):
        if m.start() > last:
            segment = text[last : m.start()]
            # Del opp lange segmenter
            for i in range(0, len(segment), NOTION_TEXT_LIMIT):
                parts.append(
                    {
                        "type": "text",
                        "text": {"content": segment[i : i + NOTION_TEXT_LIMIT]},
                    }
                )

        link_text = m.group(1)[:NOTION_TEXT_LIMIT]
        link_url = m.group(2).strip()
        if link_url.startswith(("http://", "https://")):
            parts.append(
                {
                    "type": "text",
                    "text": {"content": link_text, "link": {"url": link_url}},
                }
            )
        else:
            # Ugyldig/placeholder-URL — render som vanlig tekst (Notion avviser ellers hele kallet)
            parts.append(
                {"type": "text", "text": {"content": link_text}}
            )
        last = m.end()

    if last < len(text):
        segment = text[last:]
        for i in range(0, len(segment), NOTION_TEXT_LIMIT):
            parts.append(
                {
                    "type": "text",
                    "text": {"content": segment[i : i + NOTION_TEXT_LIMIT]},
                }
            )

    return parts or [{"type": "text", "text": {"content": ""}}]


def markdown_to_notion_blocks(text: str) -> list[dict]:
    """Enkel konvertering fra markdown til Notion-blokker."""
    blocks: list[dict] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("## "):
            content = stripped[3:].strip()
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"type": "text", "text": {"content": content}}]
                    },
                }
            )
        elif stripped.startswith("# "):
            content = stripped[2:].strip()
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_1",
                    "heading_1": {
                        "rich_text": [{"type": "text", "text": {"content": content}}]
                    },
                }
            )
        elif stripped.startswith(("• ", "- ", "* ")):
            content = stripped[2:]
            blocks.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": parse_inline_links(content)},
                }
            )
        else:
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": parse_inline_links(stripped)},
                }
            )

    return blocks


def weather_notion_blocks(weather: dict, date_human: str) -> list[dict]:
    """Bygg Notion-blokker for værseksjonen (plasseres øverst på siden)."""
    rain = weather["rain_hours"]
    rain_text = (
        "Regn over 1 mm/t: kl. " + ", ".join(rain) if rain
        else "Ingen nedbør over 1 mm i dag."
    )

    sun_periods = weather.get("sun_periods", [])
    sun_text = (
        "Full sol: " + ", ".join(sun_periods) if sun_periods
        else "Ingen klarvær i dag."
    )

    # Én linje med UV, maks temp og morgTemp
    stats_parts = []
    max_uv = weather.get("max_uv")
    max_uv_hour = weather.get("max_uv_hour")
    max_temp = weather.get("max_temp")
    max_temp_hour = weather.get("max_temp_hour")
    temp_0700 = weather.get("temp_0700")
    if max_uv is not None and max_uv_hour is not None:
        stats_parts.append(f"Maks UV: {max_uv} (kl. {max_uv_hour:02d})")
    if max_temp is not None and max_temp_hour is not None:
        stats_parts.append(f"Maks temp: {max_temp:.0f}°C (kl. {max_temp_hour:02d})")
    if temp_0700 is not None:
        stats_parts.append(f"Kl. 07: {temp_0700:.0f}°C")
    stats_text = " · ".join(stats_parts)

    def _para(text: str) -> dict:
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
        }

    blocks = [
        {
            "object": "block",
            "type": "heading_1",
            "heading_1": {
                "rich_text": [
                    {"type": "text", "text": {"content": f"Bergen — {date_human}"}}
                ]
            },
        },
        _para(weather["summary"]),
        _para(sun_text),
        _para(rain_text),
    ]
    if stats_text:
        blocks.append(_para(stats_text))
    blocks.append({"object": "block", "type": "divider", "divider": {}})
    return blocks


_ANCHOR_TEXT = "Nyhetsbriefinger"
_ARCHIVE_TITLE = "Arkiv"


def _find_child_page_by_title(notion, parent_id: str, title: str) -> str | None:
    """Finn en child_page-blokk med gitt tittel. Returnerer block_id (= page_id) eller None."""
    cursor = None
    while True:
        kwargs: dict = {"block_id": parent_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.blocks.children.list(**kwargs)
        for block in resp.get("results", []):
            if block["type"] == "child_page" and block["child_page"].get("title") == title:
                return block["id"]
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return None


def _get_or_create_archive(notion, parent_id: str, title: str = _ARCHIVE_TITLE) -> str:
    """Returner ID for arkiv-undersiden under parent_id, opprett den om nødvendig."""
    page_id = _find_child_page_by_title(notion, parent_id, title)
    if page_id:
        return page_id
    page = notion.pages.create(
        parent={"page_id": parent_id},
        properties={"title": {"title": [{"text": {"content": title}}]}},
    )
    return page["id"]


def _get_or_create_anchor(notion, parent_id: str, anchor_text: str = _ANCHOR_TEXT) -> str:
    """Returner block_id for anker-heading på parent_id, opprett den om nødvendig."""
    cursor = None
    while True:
        kwargs: dict = {"block_id": parent_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.blocks.children.list(**kwargs)
        for block in resp.get("results", []):
            if block["type"] == "heading_2":
                rt = block["heading_2"].get("rich_text", [])
                if rt and rt[0].get("text", {}).get("content") == anchor_text:
                    return block["id"]
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    result = notion.blocks.children.append(
        block_id=parent_id,
        children=[{
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": anchor_text}}]
            },
        }],
    )
    return result["results"][0]["id"]


def publish_to_notion(
    briefing: str, weather: dict, market: dict, date_str: str, date_human: str
) -> None:
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
        # Vær øverst, deretter markedsdata, deretter nyheter
        blocks = (
            weather_notion_blocks(weather, date_human)
            + market_notion_blocks(market)
            + markdown_to_notion_blocks(briefing)
        )

        # Opprett eller finn Arkiv-undersiden — briefing-sider lagres der
        archive_id = _get_or_create_archive(notion, parent_id)

        # Notion godtar maks 100 blokker per kall — del opp om nødvendig
        CHUNK = 100
        page = notion.pages.create(
            parent={"page_id": archive_id},
            properties={
                "title": {
                    "title": [{"text": {"content": f"Nyhetsbriefing {date_str}"}}]
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

        # Legg til link øverst på indekssiden — nyeste alltid først
        anchor_id = _get_or_create_anchor(notion, parent_id)
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
        print(f"\n✓  Publisert til Notion: Nyhetsbriefing {date_str}")
        if page_url:
            print(f"   {page_url}")
    except Exception as exc:
        print(f"✗  Notion-feil: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Hovedprogram
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Daglig nyhetsbriefing med Claude AI")
    parser.add_argument(
        "--save",
        action="store_true",
        help="Lagre briefingen som en markdown-fil (briefing_YYYY-MM-DD.md)",
    )
    args = parser.parse_args()

    _load_dotenv()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Feil: ANTHROPIC_API_KEY er ikke satt.")
        sys.exit(1)

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_human = datetime.now().strftime("%A %d. %B %Y")

    print(f"\n{'─'*70}")
    print(f"  Nyhetsbriefing  —  {today_human}")
    print(f"{'─'*70}\n")

    print("Henter vær (Bergen, Oslo, Alicante)...")
    weather, weather_alt = fetch_all_weather()
    print(f"  Bergen: {weather['summary']}")
    if weather["rain_hours"]:
        print(f"  Regn over 1 mm/t: kl. {', '.join(weather['rain_hours'])}")
    for key, w in weather_alt.items():
        print(f"  {WEATHER_LOCATIONS[key]['name']}: {w['summary']}")
    print()

    print("Henter markedsdata...")
    market = fetch_market_snapshot()
    if market["error"]:
        print(f"  ⚠  Markedsdata utilgjengelig: {market['error']}")
    else:
        def _sign(v):
            return f"{v:+.1f} %" if v is not None else "–"
        brent_str = f"{market['brent']:.1f} $" if market["brent"] else "–"
        sp_str = f"{market['sp500']:,.0f}".replace(",", " ") if market["sp500"] else "–"
        ob_str = f"{market['osebx']:,.0f}".replace(",", " ") if market["osebx"] else "–"
        btc_str = f"{market['btc']:,.0f}".replace(",", " ") + " $" if market.get("btc") else "–"
        eth_str = f"{market['eth']:,.0f}".replace(",", " ") + " $" if market.get("eth") else "–"
        nordnet_str = f"{market['nordnet']:,.1f}".replace(",", " ") if market.get("nordnet") else "–"
        print(
            f"  Brent {brent_str} ({_sign(market['brent_chg'])})  "
            f"S&P 500 {sp_str} ({_sign(market['sp500_chg'])})  "
            f"OBX {ob_str} ({_sign(market['osebx_chg'])})"
        )
        print(
            f"  BTC {btc_str} ({_sign(market.get('btc_chg'))})  "
            f"ETH {eth_str} ({_sign(market.get('eth_chg'))})  "
            f"Nordnet Global {nordnet_str} ({_sign(market.get('nordnet_chg'))})"
        )
    print()

    print("Henter dagens quiz (lokalt bibliotek)...")
    quiz = fetch_daily_quiz()
    if quiz:
        cats = ", ".join(q["category"] for q in quiz)
        print(f"  {len(quiz)} spørsmål ({cats})")
    else:
        print("  ⚠  ingen quiz i dag")
    print()

    print("Genererer dagens gåter (Claude)...")
    riddles = fetch_daily_riddles()
    print(f"  {len(riddles)} gåter" if riddles else "  ⚠  ingen gåter i dag")
    print()

    print("Henter SK Brann-status (NIFS)...")
    brann = fetch_brann_info()
    if brann:
        t = brann.get("table") or {}
        nm = brann.get("next_match") or {}
        print(
            f"  {t.get('place', '?')}. plass, {t.get('points', '?')} poeng"
            + (f" — neste: {'hjemme mot' if nm.get('home') else 'borte mot'} {nm.get('opponent')}" if nm else "")
        )
    else:
        print("  ⚠  ingen Brann-info i dag")
    print()

    print("Kuraterer dagens læring (podcasts + boktips, Claude)...")
    learning = fetch_daily_learning()
    if learning:
        print(
            f"  {len(learning.get('podcasts', []))} podcast-råd, "
            f"{len(learning.get('books', []))} boktips"
        )
    else:
        print("  ⚠  ingen læring i dag")
    print()

    print(f"Henter nyheter fra {len(RSS_FEEDS)} kilder...")
    prev = _load_recent_briefing_points()
    if prev["points"]:
        print(
            f"  ⓘ  dedup mot siste {NEWS_HISTORY_DAYS} dagers briefinger: "
            f"{len(prev['points'])} tidligere punkter"
        )
    articles = fetch_articles(skip=prev)

    if not articles:
        print("\nIngen artikler funnet. Sjekk internettforbindelsen og RSS-URLene.")
        sys.exit(1)

    print(f"\nTotalt {len(articles)} artikler fra siste {LOOKBACK_HOURS} timer.")

    briefing = summarize_with_claude(articles, prev["points"])

    print("─" * 70)

    print("\nGenererer dagens refleksjonsspørsmål (Claude)...")
    reflection = fetch_daily_reflection(briefing, learning)
    print(
        f"  {len(reflection)} refleksjonsspørsmål"
        if reflection
        else "  ⚠  ingen refleksjon i dag"
    )

    # Lagre dagens briefing til datalageret som nettsiden leser
    store_briefing(
        today_str, news_md=briefing, weather=weather,
        weather_alt=weather_alt or None, market=market,
        quiz=quiz or None, riddles=riddles or None,
        learning=learning, brann=brann, reflection=reflection or None,
    )

    # Notion
    has_notion = (
        "NOTION_API_KEY" in os.environ and "NOTION_PARENT_PAGE_ID" in os.environ
    )
    if has_notion:
        publish_to_notion(briefing, weather, market, today_str, today_human)
    else:
        print(
            "\n💡  Tips: Sett NOTION_API_KEY og NOTION_PARENT_PAGE_ID "
            "for å publisere automatisk til Notion."
        )

    # Lagre som fil
    if args.save:
        rain_line = (
            "Regn over 1 mm/t: kl. " + ", ".join(weather["rain_hours"])
            if weather["rain_hours"]
            else "Ingen nedbør over 1 mm i dag."
        )
        weather_md = f"## Bergen\n{weather['summary']}\n{rain_line}\n\n"
        data_dir = os.environ.get("BRIEFING_DATA_DIR", ".")
        filename = os.path.join(data_dir, f"briefing_{today_str}.md")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"# Nyhetsbriefing — {today_human}\n\n" + weather_md + briefing)
        print(f"✓  Lagret som {filename}")


if __name__ == "__main__":
    main()
