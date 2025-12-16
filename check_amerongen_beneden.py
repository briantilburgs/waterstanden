#!/usr/bin/env python3
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = "https://ddapi20-waterwebservices.rijkswaterstaat.nl"
URL_CHECK = f"{BASE}/ONLINEWAARNEMINGENSERVICES/CheckWaarnemingenAanwezig"
URL_OBS = f"{BASE}/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen"

TZ = ZoneInfo("Europe/Amsterdam")
LOCATION_CODE = "amerongen.beneden"


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def post_json(url: str, payload: dict) -> dict:
    r = requests.post(url, json=payload, timeout=20)

    # 204 No Content = valid request, but no matching data
    if r.status_code == 204:
        return {}

    r.raise_for_status()

    if not r.text or not r.text.strip():
        return {}

    try:
        return r.json()
    except ValueError:
        raise ValueError(
            f"Non-JSON response from {url}. Content-Type={r.headers.get('Content-Type')}\n"
            f"Body (first 1000 chars):\n{r.text[:1000]}"
        )


def check_waterstand_amerongen():
    # Waterinfo publiek: ~28 dagen terug, ~2 dagen vooruit.
    # Voor een simpele beschikbaarheidscheck: neem laatste 2 dagen.
    now = datetime.now(TZ)
    start = now - timedelta(days=2)
    end = now

    # 1) CheckWaarnemingenAanwezig (DD-API20 format)
    check_payload = {
        "LocatieLijst": [{"Code": LOCATION_CODE}],
        "AquoMetadataLijst": [
            {
                "Compartiment": {"Code": "OW"},
                "Grootheid": {"Code": "WATHTE"},
            }
        ],
        "Periode": {
            "Begindatumtijd": iso(start),
            "Einddatumtijd": iso(end),
        },
    }

    check_resp = post_json(URL_CHECK, check_payload)
    aanwezig = str(check_resp.get("WaarnemingenAanwezig", "false")).lower() == "true"

    if not aanwezig:
        print("❌ Geen waterstand beschikbaar (WATHTE) voor Amerongen beneden in de afgelopen 2 dagen")
        print("   CheckWaarnemingenAanwezig response:")
        print(json.dumps(check_resp, indent=2, ensure_ascii=False))
        return

    print("✅ Waterstand lijkt beschikbaar (CheckWaarnemingenAanwezig=true). Haal laatste waarde op...")

    # 2) OphalenWaarnemingen (DD-API20 format)
    obs_payload = {
        "Locatie": {"Code": LOCATION_CODE},
        "AquoPlusWaarnemingMetadata": {
            "AquoMetadata": {
                "Compartiment": {"Code": "OW"},
                "Grootheid": {"Code": "WATHTE"},
                "ProcesType": "meting",
            }
        },
        "Periode": {
            "Begindatumtijd": iso(start),
            "Einddatumtijd": iso(end),
        },
    }

    obs_resp = post_json(URL_OBS, obs_payload)
    wlists = obs_resp.get("WaarnemingenLijst", []) or []
    if not wlists:
        print("⚠️ Check zei dat er data is, maar OphalenWaarnemingen gaf geen WaarnemingenLijst terug.")
        print(json.dumps(obs_resp, indent=2, ensure_ascii=False)[:4000])
        return

    # Neem de eerste lijst, en pak de laatste waarneming (meestal gesorteerd op tijd)
    waarn = (wlists[0].get("Waarnemingen") or [])
    if not waarn:
        print("⚠️ WaarnemingenLijst aanwezig, maar leeg.")
        print(json.dumps(wlists[0], indent=2, ensure_ascii=False)[:4000])
        return

    last = waarn[-1]
    tijd = last.get("Datumtijd")
    waarde = (last.get("Meetwaarde") or {}).get("Waarde_Numeriek")

    print("✅ Waterstand beschikbaar voor Amerongen beneden")
    print(f"   Tijdstip : {tijd}")
    print(f"   Waarde   : {waarde}")


if __name__ == "__main__":
    check_waterstand_amerongen()
