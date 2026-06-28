"""Verifiser at dagens briefing faktisk ble skrevet — fang «stille feil».

Kjøres på slutten av docker-entrypoint.sh, rett etter generatoren. Sjekker selve
dataproduktet (JSON-fila) i stedet for exitkoder, så det fanger at Claude var nede,
at ingen artikler ble funnet, eller at scriptet krasjet før det skrev fila.

- news_md mangler/tom  → hard feil (varsler ALERT_WEBHOOK_URL), ingen heartbeat.
- research_md mangler  → kun en notis (kan være en stille dag uten nye studier).
- alt ok               → pinger HEARTBEAT_URL (dead man's switch).
"""
import json
import os
from datetime import datetime

from notify import send_alert, send_heartbeat


def main() -> None:
    data_dir = os.environ.get("BRIEFING_DATA_DIR", ".")
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(data_dir, "briefings", f"{today}.json")

    if not os.path.exists(path):
        send_alert(f"Ingen briefing-fil for {today} ({path}) — generatoren produserte ikke data.")
        print(f"[healthcheck] FEIL: mangler {path}")
        return

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        send_alert(f"Briefing-fila for {today} er ugyldig JSON: {exc}")
        print(f"[healthcheck] FEIL: ugyldig JSON ({exc})")
        return

    if not (data.get("news_md") or "").strip():
        send_alert(f"Briefing for {today}: news_md mangler/tom — nyhetsgeneratoren feilet.")
        print("[healthcheck] FEIL: news_md mangler/tom")
        return

    if not (data.get("research_md") or "").strip():
        print(f"[healthcheck] advarsel: research_md mangler for {today} (kan vaere en stille dag uten nye studier)")

    print(f"[healthcheck] OK: {today} - briefing skrevet")
    send_heartbeat()


if __name__ == "__main__":
    main()
