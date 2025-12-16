#!/usr/bin/env python3
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = "https://ddapi20-waterwebservices.rijkswaterstaat.nl"
URL_CHECK = f"{BASE}/ONLINEWAARNEMINGENSERVICES/CheckWaarnemingenAanwezig"
URL_OBS = f"{BASE}/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen"
RED = "\033[31m"
GREEN = "\033[32m"
BLUE = "\033[34m"
RESET = "\033[0m"
TZ = ZoneInfo("Europe/Amsterdam")
LOCATIONS = {
    "lobith": "lobith.bovenrijn.haven",
    "amerongen beneden": "amerongen.beneden",
    "Krimpen a/d IJssel": "krimpenaandeijssel.hollandscheijssel",
}


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def post_json(url: str, payload: dict) -> dict:
    r = requests.post(url, json=payload, timeout=20)
    print(f"Getting data from {url}")
    # 204 No Content = valid request, but no matching data
    if r.status_code == 204:
        return {}

    r.raise_for_status()
    print(f"{r.status_code} - ")

    if not r.text or not r.text.strip():
        return {}

    try:
        filedata = r.json()
        with open("testdata.json", "w", encoding="utf-8") as f:
            json.dump(filedata, f, indent=2, ensure_ascii=False)
        return r.json()
    except ValueError:
        raise ValueError(
            f"Non-JSON response from {url}. Content-Type={r.headers.get('Content-Type')}\n"
            f"Body (first 1000 chars):\n{r.text[:1000]}"
        )


def check_waterstand(label: str, location_code: str):
    # Waterinfo publiek: ~28 dagen terug, ~2 dagen vooruit.
    # Voor een simpele beschikbaarheidscheck: neem laatste 2 dagen.
    now = datetime.now(TZ)
    start = now - timedelta(days=2)
    end = now

    # 1) CheckWaarnemingenAanwezig (DD-API20 format) alleen kort:
    # Expecting:
    # {
    #  "Succesvol": true,
    #  "WaarnemingenAanwezig": "true"
    # }
    check_payload = {
        "LocatieLijst": [{"Code": location_code}],
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
        print(f"❌ Geen waterstand beschikbaar (WATHTE) voor {label} in de afgelopen 2 dagen")
        print("   CheckWaarnemingenAanwezig response:")
        print(json.dumps(check_resp, indent=2, ensure_ascii=False))
        return

    print(f"✅ Waterstand lijkt beschikbaar voor {label} (CheckWaarnemingenAanwezig=true). Haal laatste waarde op...")

    # 2) OphalenWaarnemingen (DD-API20 format)
    obs_payload = {
        "Locatie": {"Code": location_code},
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
        print(json.dumps(obs_resp, indent=2, ensure_ascii=False)[:100])
        return

    # Neem de eerste lijst, en pak de laatste waarneming (meestal gesorteerd op tijd)
    waarn = (wlists[0].get("MetingenLijst") or [])
    if not waarn:
        print("⚠️ WaarnemingenLijst aanwezig, maar leeg.")
        print(json.dumps(wlists[0], indent=2, ensure_ascii=False)[:100])
        return

    last = waarn[-1]
    tijd = last.get("Datumtijd")
    waarde = (last.get("Meetwaarde") or {}).get("Waarde_Numeriek")

    print(f"✅ Waterstand beschikbaar voor {label}")
    print(f"   Tijdstip : {tijd}")
    print(f"   Waarde   : {waarde}")
    return wlists

def print_data(water_data):
    # Deze functie print de beschrikbare water data
    print_dict={'index':{}}
    for loc_data in water_data:
        locatie = loc_data[0]['Locatie']['Code']
        print_dict[locatie] = {}
        print(f"starting measurements for {locatie}")

        for meting in loc_data[0]["MetingenLijst"]:
            timestamp = meting["Tijdstip"][:13]
            print_dict['index'][timestamp] = {}
            print_dict[locatie][timestamp] = meting["Meetwaarde"]["Waarde_Numeriek"]

    print(print_dict)
    return print_dict

def print_table(data: dict):
    times = data["index"].keys()
    locations = [k for k in data.keys() if k != "index"]

    # header
    header = ["Tijd"] + locations
    widths = [len(h) for h in header]

    rows = []
    for t in times:
        row = [t]
        for loc in locations:
            val = data.get(loc, {}).get(t, "")
            row.append("" if val == "" else f"{val:.1f}")
        rows.append(row)
        widths = [max(w, len(c)) for w, c in zip(widths, row)]

    def fmt(row):
        cells = []
        for i, (c, w) in enumerate(zip(row, widths)):
            if i == 0:  # Tijd links
                cells.append(c.ljust(w))
            else:       # Getallen rechts
                cells.append(c.rjust(w))
        return "| " + " | ".join(cells) + " |"
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"

    print(sep)
    print(fmt(header))
    print(sep)
    for r in rows:
        print(fmt(r))
    print(sep)

def main():
    waterstanden = []
    water_data = {}
    for label, code in LOCATIONS.items():
        print("\n" + "=" * 60)
        print(f"Locatie: {label} ({code})")
        print("=" * 60)
        waterstanden.append(check_waterstand(label, code))


    printable_dict = print_data(waterstanden)
    print_table(printable_dict)

if __name__ == "__main__":
    main()