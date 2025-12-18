#!/usr/bin/env python3
import json
import requests
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from zoneinfo import ZoneInfo

BASE = "https://ddapi20-waterwebservices.rijkswaterstaat.nl"
URL_CHECK = f"{BASE}/ONLINEWAARNEMINGENSERVICES/CheckWaarnemingenAanwezig"
URL_OBS = f"{BASE}/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
ORANGE = "\033[38;5;208m"
BLACK = "\033[90m"
RESET = "\033[0m"
TZ = ZoneInfo("Europe/Amsterdam")
LOCATIONS = {
    "Lobith, Bovenrijn, haven": "lobith.bovenrijn.haven",
    "Driel, boven": "driel.boven",
    "Driel, beneden": "driel.beneden",
    "Rhenen Grebbeberg": "rhenen.grebbeberg",
    "Amerongen, boven": "amerongen.boven",
    "Amerongen, beneden": "amerongen.beneden",
    "Culemborg": "culemborg",
    "Krimpen a/d IJssel": "krimpenaandeijssel.hollandscheijssel",
}
ALARMS = {
    "Lobith, Bovenrijn, haven": {"max_level": 1100.0, "norm_level": 800.0, "alarm": "GREEN"},
    "Amerongen, beneden": {"max_level": 425.0, "norm_level": 150.0, "alarm": "GREEN"},
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
        return r.json()
    except ValueError:
        raise ValueError(
            f"Non-JSON response from {url}. Content-Type={r.headers.get('Content-Type')}\n"
            f"Body (first 1000 chars):\n{r.text[:1000]}"
        )


def check_waterstand(label: str, location_code: str, type_data: str, days: int):
    # Waterinfo publiek: ~28 dagen terug, ~2 dagen vooruit.
    # Voor een simpele beschikbaarheidscheck: neem laatste 2 dagen.
    # type data == "meting", "verwachting",
    now = datetime.now(TZ)
    if type_data == "meting":
        start = now - timedelta(days=days)
        end = now
    elif type_data == "verwachting":
        start = now
        end = now + timedelta(days=days)
    else:
        return {}
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

    print(f"✅ Waterstand lijkt beschikbaar voor {label} (CheckWaarnemingenAanwezig=true). Haal laatste waarde op...")

    # 2) OphalenWaarnemingen (DD-API20 format)
    obs_payload = {
        "Locatie": {"Code": location_code},
        "AquoPlusWaarnemingMetadata": {
            "AquoMetadata": {
                "Compartiment": {"Code": "OW"},
                "Grootheid": {"Code": "WATHTE"},
                "ProcesType": f"{type_data}",
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
        print("⚠️ MetingenLijst aanwezig, maar leeg.")
        print(json.dumps(wlists[0], indent=2, ensure_ascii=False)[:100])
        return

    last = waarn[-1]
    tijd = last.get("Datumtijd")
    waarde = (last.get("Meetwaarde") or {}).get("Waarde_Numeriek")

    print(f"✅ Waterstand beschikbaar voor {label}")
    print(f"   Tijdstip : {tijd}")
    print(f"   Waarde   : {waarde}")
    return wlists


def create_print_data(water_data):
    # Deze functie converteerd de beschrikbare water data in een overzichtelijke vorm
    print_dict = {'index': {}}
    for loc_data in water_data:
        locatie = loc_data['Locatie']['Naam']
        print_dict.setdefault(locatie, {})
        print(f"starting measurements for {locatie}")

        if loc_data.get("MetingenLijst"):
            for meting in loc_data["MetingenLijst"]:
                timestamp = meting["Tijdstip"][:13]
                print_dict['index'][timestamp] = {}
                print_dict[locatie][timestamp] = meting["Meetwaarde"]["Waarde_Numeriek"]

    return print_dict


def print_table(data: dict):
    times = list(data["index"].keys())
    locations = [k for k in data.keys() if k != "index"]

    header = ["Tijd"] + locations

    # Bepaal breedte op basis van Tijd-kolom
    col_width = max(len("Tijd"), max(len(t) for t in times))
    widths = [col_width] * len(header)

    def color(curr, prev, text):
        if prev is None:
            return text
        if curr > prev:
            return f"{RED}{text}{RESET}"
        if curr < prev:
            return f"{GREEN}{text}{RESET}"
        return f"{BLUE}{text}{RESET}"

    def fmt(row):
        cells = []
        for i, (c, w) in enumerate(zip(row, widths)):
            if i == 0:  # Tijd links
                cells.append(c.ljust(w))
            else:  # Getallen rechts
                cells.append(c.rjust(w))
        return "| " + " | ".join(cells) + " |"

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"

    print(sep)
    print(fmt(header))
    print(sep)

    prev_values = {loc: None for loc in locations}

    for t in times:
        row = [t]
        for loc in locations:
            v = data.get(loc, {}).get(t)
            if v is None:
                row.append("")
            else:
                cell = f"{v:>13.1f}"
                txt = color(v, prev_values[loc], cell)
                row.append(txt)
                prev_values[loc] = v
        print(fmt(row))

    print(sep)


def plot_waterstanden(data: dict, title="Waterstanden"):
    now = datetime.now(TZ)

    times = list(data["index"].keys())
    x = [datetime.strptime(t, "%Y-%m-%dT%H") for t in times]

    locations = [k for k in data.keys() if k != "index"]

    plt.figure(figsize=(14, 10))
    # Create Grapfh layout
    ax = plt.gca()
    # Major ticks: elke dag om 00:00
    ax.xaxis.set_major_locator(mdates.DayLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    # Minor ticks: elke 6 uur
    ax.xaxis.set_minor_locator(mdates.HourLocator(interval=6))
    # Rasterlijnen
    ax.grid(which="major", linewidth=1.2)  # dikker (00:00)
    ax.grid(which="minor", linewidth=0.4)  # dunner (6 uur)

    for loc in locations:
        y = [data.get(loc, {}).get(t, None) for t in times]
        plt.plot(x, y, marker=".", markersize=3, linewidth=1, label=loc)

    plt.axhline(425.0, color="red", linestyle="--", linewidth=1)
    plt.axhline(1100.0, color="red", linestyle="--", linewidth=1)
    plt.axvline(mdates.date2num(now), color="red", linestyle="--", linewidth=1)
    plt.title(title)
    plt.xlabel("Tijd")
    plt.ylabel("Waterstand (cm)")
    plt.xticks(rotation=45, ha="right")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig("waterstanden.png", dpi=150, bbox_inches="tight")
    plt.show()

def check_alarms(data: dict):
    """

    Args:
        data (dict): All water data

    Returns:

    """
    print(f"Lets Check Alarms")
    alarm_on = False
    now = datetime.now(TZ)
    now = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    for site in ALARMS:
        alarm_local = "GREEN"
        print(f"  {site}")
        site_data = data.get(site)
        if not isinstance(site_data, dict):
            print(f"    ⚠️ Geen data gevonden voor alarm-locatie '{site}'.")
            ALARMS[site]["alarm"] = alarm_local
            continue
        for time, hight in site_data.items():
            # print(f"{now} <= {datetime.strptime(time, "%Y-%m-%dT%H").replace(tzinfo=TZ)}")
            if now <= datetime.strptime(time, "%Y-%m-%dT%H").replace(tzinfo=TZ):
                max_level = float(ALARMS[site]["max_level"])
                norm_level = float(ALARMS[site]["norm_level"])
                span = max_level - norm_level
                h = float(hight)

                # Multi-level warning states based on norm_level + % of (max_level - norm_level)
                if h >= max_level:
                    state = "BLACK"
                elif h >= norm_level + 0.95 * span:
                    state = "RED"
                elif h >= norm_level + 0.75 * span:\
                    state = "ORANGE"
                elif h >= norm_level + 0.50 * span:
                    state = "YELLOW"
                else:
                    state = "GREEN"

                # Keep the worst state across times for this site
                order = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3, "BLACK": 4}
                if order[state] > order[alarm_local]:
                    alarm_local = state

                state_color = {
                    "GREEN": GREEN,
                    "YELLOW": YELLOW,
                    "ORANGE": ORANGE,
                    "RED": RED,
                    "BLACK": BLACK,
                }

                if state == "GREEN":
                    print(f"    {GREEN}OK{RESET} {site}: {time}: {hight}")
                else:
                    prefix = f"{state_color.get(state, '')}WARNING {state}{RESET}"
                    print(
                        f"    {prefix} {site}: at {time} {hight} "
                        f"(norm={norm_level}, max={max_level}, span={span})"
                    )
                    alarm_on = True
        ALARMS[site]["alarm"] = alarm_local
    return alarm_on


def main():
    waterstanden = []
    for label, code in LOCATIONS.items():
        print("\n" + "=" * 60)
        print(f"Locatie: {label} ({code})")
        print("=" * 60)

        for t in ("meting", "verwachting"):
            r = check_waterstand(label, code, t, 30)
            if isinstance(r, list):
                waterstanden.extend(r)

    with open("waterstanden.json", "w", encoding="utf-8") as f:
        json.dump(waterstanden, f, indent=2, ensure_ascii=False)

    printable_dict = create_print_data(waterstanden)
    # print_table(printable_dict)
    plot_waterstanden(printable_dict)
    if check_alarms(printable_dict) is True:
        print(f"Alarm found: {ALARMS}")


if __name__ == "__main__":
    main()
