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


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — legg til / fjern RSS-feeds her
# ─────────────────────────────────────────────────────────────────────────────

RSS_FEEDS: dict[str, str] = {
    "NRK Nyheter": "https://www.nrk.no/nyheter/rss.xml",
    "NRK Økonomi": "https://www.nrk.no/okonomi/rss.xml",
    "Bergens Tidende": "https://www.bt.no/rss.xml",
    "E24": "https://e24.no/rss.xml",
    "Finansavisen": "https://finansavisen.no/feed/",
    "Reuters Top News": "https://feeds.reuters.com/reuters/topNews",
    "Reuters Business": "https://feeds.reuters.com/reuters/businessnews",
    "BBC World": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "BBC Business": "http://feeds.bbci.co.uk/news/business/rss.xml",
    "Dagens Næringsliv": "https://www.dn.no/rss.xml",
    "Oslo Børs nyheter": "https://www.oslobors.no/ob/servlets/rss?category=nyhet",
}

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096
LOOKBACK_HOURS = 24
MAX_PER_FEED = 15  # maks antall artikler per kilde
MAX_DESC_CHARS = 400  # maks tegn fra ingress/beskrivelse per artikkel

SYSTEM_PROMPT = """Nyhetsbriefing på norsk for en investor i Bergen. Skriv som en Bloomberg-terminal: tall og fakta, null pynt.

FORMAT:
- Fire seksjoner med ## heading og • kulepunkter — ingenting annet.
- Maks 3 punkter per seksjon. Heller færre enn å fylle opp med svake nyheter.
- Én setning per punkt. Subjekt + verb + tall/konsekvens. Slutt.
- Alltid inline-lenke: [tittel](url)
- Tom seksjon → skriv kun: • Ingen viktige hendelser.

FORBUDT I OUTPUT:
- Fyllfraser: "Det er verdt å merke seg", "I tillegg", "Som et resultat", "Det er viktig å"
- Gjentakelse av kildenavn, dato eller kontekst fra forrige punkt
- Vurderinger og adjektiver som ikke er tall: "betydelig", "kraftig", "stor"

KUTT ALLTID: sport, kjendis, krim, underholdning, vær, lokale ulykker, politisk debatt uten vedtak.

## 📈 Marked og makro
Markedsdata (priser og prosentendringer) vises allerede i et eget snapshot øverst — IKKE gjenta prisene.
Ta med: rentevedtak, inflasjon, handelskrig, HVORFOR markedet beveget seg, kvartalstall som beveger markedet.
Kutt: dagsbevegelser uten nyhet bak.

## 🇳🇴 Norsk økonomi
Ta med: Norges Bank, statsbudsjett, norske selskaper med markedseffekt, oljesektor, kronekurs med årsak.
Kutt: NRK, kultur, innenrikspolitikk uten økonomisk utfall.

## 🏙️ Bergen og Vestland
Ta med KUN direkte hverdagskonsekvens:
✓ Kollektivstreik/-stans (Skyss, Bybanen, buss)
✓ Veistenging / store trafikkforstyrrelser
✓ Lokale prisendringer (bolig, kommunale avgifter)
✓ Kommunevedtak (barnehage, skole, helse)
✓ Helseadvarsler / sykehuskapasitet
✓ Store arbeidsplassnyheter (nedleggelse / nyetablering)

## 🌍 Internasjonalt
Ta med: krig/konflikt med geopolitisk spillover, store naturkatastrofer, valg/regjeringsskifte i G20.
Kutt: alt annet.

TOTALBUDSJETT: Maks 300 ord for alle fire seksjoner samlet."""

# ─────────────────────────────────────────────────────────────────────────────
# Værvarsling Bergen (Yr / MET Norway API)
# ─────────────────────────────────────────────────────────────────────────────

# Bergen: 60.3928°N, 5.3241°E
_YR_URL = (
    "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=60.3928&lon=5.3241"
)

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


def fetch_bergen_weather() -> dict:
    """
    Returnér værvarsling for Bergen som dict:
      summary   — én kort linje (nåværende + ettermiddag)
      rain_hours — liste med tidsspenn der nedbør >= 1 mm/t resten av dagen
    """
    try:
        resp = httpx.get(
            _YR_URL,
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

        # Finn timer med nedbør >= 1 mm/t resten av i dag
        rain_hours: list[str] = []
        for entry in ts:
            t_local = datetime.fromisoformat(
                entry["time"].replace("Z", "+00:00")
            ).astimezone()
            if t_local.date() > today_date:
                break
            if t_local < now_local:
                continue
            d = entry["data"]
            if "next_1_hours" in d:
                mm = d["next_1_hours"]["details"].get("precipitation_amount", 0.0)
                if mm >= 1.0:
                    rain_hours.append(f"{t_local.hour:02d}–{t_local.hour + 1:02d}")

        return {"summary": summary, "rain_hours": rain_hours}

    except Exception as exc:
        return {"summary": f"utilgjengelig ({exc})", "rain_hours": []}


# ─────────────────────────────────────────────────────────────────────────────
# Markedssnapshot (yfinance / Yahoo Finance)
# ─────────────────────────────────────────────────────────────────────────────


def fetch_market_snapshot() -> dict:
    """
    Returnér markedsdata som dict. Myk feil — stopper ikke kjøringen.
    Keys: brent, brent_chg, sp500, sp500_chg, osebx, osebx_chg,
          eurnok, usdnok, error
    """
    try:
        import logging
        import yfinance as yf

        logging.getLogger("yfinance").setLevel(logging.ERROR)

        t = yf.Tickers("BZ=F ^GSPC OBX.OL EURNOK=X USDNOK=X")

        def _pct(info) -> tuple:
            last = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)
            chg = (last / prev - 1) * 100 if last and prev else None
            return last, chg

        brent, brent_chg = _pct(t.tickers["BZ=F"].fast_info)
        sp500, sp500_chg = _pct(t.tickers["^GSPC"].fast_info)
        osebx, osebx_chg = _pct(t.tickers["OBX.OL"].fast_info)
        eurnok = getattr(t.tickers["EURNOK=X"].fast_info, "last_price", None)
        usdnok = getattr(t.tickers["USDNOK=X"].fast_info, "last_price", None)

        return {
            "brent": brent, "brent_chg": brent_chg,
            "sp500": sp500, "sp500_chg": sp500_chg,
            "osebx": osebx, "osebx_chg": osebx_chg,
            "eurnok": eurnok, "usdnok": usdnok,
            "error": None,
        }
    except Exception as exc:
        return {
            "brent": None, "brent_chg": None,
            "sp500": None, "sp500_chg": None,
            "osebx": None, "osebx_chg": None,
            "eurnok": None, "usdnok": None,
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
        fx1 = f"{market['eurnok']:.2f}" if market["eurnok"] else "–"
        fx2 = f"{market['usdnok']:.2f}" if market["usdnok"] else "–"
        line2 = f"EUR/NOK {fx1}  ·  USD/NOK {fx2}"
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
# RSS-henting
# ─────────────────────────────────────────────────────────────────────────────


def fetch_articles() -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    articles: list[dict] = []

    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(
                url, request_headers={"User-Agent": "news-briefing/1.0"}
            )
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


def summarize_with_claude(articles: list[dict]) -> str:
    client = anthropic.Anthropic()  # les ANTHROPIC_API_KEY automatisk fra env

    today_str = datetime.now().strftime("%A %d. %B %Y")
    articles_text = build_articles_text(articles)

    user_content = (
        f"Dato: {today_str}\n\n"
        f"Totalt {len(articles)} artikler fra siste {LOOKBACK_HOURS} timer:\n\n"
        f"{articles_text}"
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
        link_url = m.group(2)
        parts.append(
            {
                "type": "text",
                "text": {"content": link_text, "link": {"url": link_url}},
            }
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
    if rain:
        rain_text = "Regn over 1 mm/t: kl. " + ", ".join(rain)
    else:
        rain_text = "Ingen nedbør over 1 mm i dag."

    return [
        {
            "object": "block",
            "type": "heading_1",
            "heading_1": {
                "rich_text": [
                    {"type": "text", "text": {"content": f"Bergen — {date_human}"}}
                ]
            },
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": weather["summary"]}}]
            },
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": rain_text}}]
            },
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]


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

        # Notion godtar maks 100 blokker per kall — del opp om nødvendig
        CHUNK = 100
        page = notion.pages.create(
            parent={"page_id": parent_id},
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

    print("Henter vær for Bergen...")
    weather = fetch_bergen_weather()
    print(f"  Bergen: {weather['summary']}")
    if weather["rain_hours"]:
        print(f"  Regn over 1 mm/t: kl. {', '.join(weather['rain_hours'])}")
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
        print(
            f"  Brent {brent_str} ({_sign(market['brent_chg'])})  "
            f"S&P 500 {sp_str} ({_sign(market['sp500_chg'])})  "
            f"OBX {ob_str} ({_sign(market['osebx_chg'])})"
        )
        fx1 = f"{market['eurnok']:.2f}" if market["eurnok"] else "–"
        fx2 = f"{market['usdnok']:.2f}" if market["usdnok"] else "–"
        print(f"  EUR/NOK {fx1}  USD/NOK {fx2}")
    print()

    print(f"Henter nyheter fra {len(RSS_FEEDS)} kilder...")
    articles = fetch_articles()

    if not articles:
        print("\nIngen artikler funnet. Sjekk internettforbindelsen og RSS-URLene.")
        sys.exit(1)

    print(f"\nTotalt {len(articles)} artikler fra siste {LOOKBACK_HOURS} timer.")

    briefing = summarize_with_claude(articles)

    print("─" * 70)

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
        filename = f"briefing_{today_str}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"# Nyhetsbriefing — {today_human}\n\n" + weather_md + briefing)
        print(f"✓  Lagret som {filename}")


if __name__ == "__main__":
    main()
