"""Valgfri varsling for den daglige generatoren (begge env-vars er valgfrie).

- ALERT_WEBHOOK_URL: POSTes en melding når noe er galt (f.eks. ingen briefing-fil).
  Payloaden er {"text": ..., "content": ...} så den passer både Slack og Discord
  (begge ignorerer den nøkkelen de ikke bruker).
- HEARTBEAT_URL: pinges (GET) ved vellykket kjøring. Pek den mot en ekstern
  «dead man's switch» (f.eks. healthchecks.io) som varsler deg hvis pinget UTEBLIR
  — da fanger du også tilfellet der cron aldri kjørte i det hele tatt.

Uten env-var skjer ingenting (myk). httpx er allerede en avhengighet.
"""
import os
import socket


def _host() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "ukjent"


def send_alert(message: str) -> None:
    url = os.environ.get("ALERT_WEBHOOK_URL")
    if not url:
        return
    text = f"⚠️ Nyhetsbriefing ({_host()}): {message}"
    try:
        import httpx

        httpx.post(url, json={"text": text, "content": text}, timeout=10)
        print(f"  [varsel sendt] {message}")
    except Exception as exc:
        print(f"  [feil] klarte ikke sende varsel: {exc}")


def send_heartbeat() -> None:
    url = os.environ.get("HEARTBEAT_URL")
    if not url:
        return
    try:
        import httpx

        httpx.get(url, timeout=10)
        print("  [heartbeat sendt]")
    except Exception as exc:
        print(f"  [feil] klarte ikke sende heartbeat: {exc}")
