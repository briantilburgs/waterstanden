#!/usr/bin/env python3
import sys
import json
import requests
from pathlib import Path
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

BASE = "https://ddapi20-waterwebservices.rijkswaterstaat.nl"
URL_CATALOG = f"{BASE}/METADATASERVICES/OphalenCatalogus"
URL_OBS = f"{BASE}/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen"
URL_LAST = f"{BASE}/ONLINEWAARNEMINGENSERVICES/OphalenLaatsteWaarnemingen"
URL_CHECK = f"{BASE}/ONLINEWAARNEMINGENSERVICES/CheckWaarnemingenAanwezig"

TZ = ZoneInfo("Europe/Amsterdam")

TARGETS = [
    "lobith",
    "amerongen beneden",
    "kinderdijk",
]

def iso(dt: datetime) -> str:
    # API accepteert ISO 8601; inclusief timezone offset is handig
    return dt.isoformat(timespec="milliseconds")

def post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    r = requests.post(url, json=payload, timeout=timeout)

    # 204 No Content = geldige request, maar geen matchende data
    if r.status_code == 204:
        return {}

    r.raise_for_status()

    # Soms komt er een lege body terug; behandel dat als 'geen data'
    if not r.text or not r.text.strip():
        return {}

    try:
        return r.json()
    except ValueError:
        raise ValueError(
            f"Response was not valid JSON from {url}. Content-Type={r.headers.get('Content-Type')}\n"
            f"Body (first 1000 chars):\n{r.text[:1000]}"
        )

def find_location_codes(catalog: dict, wanted_names: list[str]) -> dict:
    """
    Probeert per targetnaam een locatie te vinden op basis van Naam/Code (case-insensitive).
    Neemt de 'beste' match (exacte naam-match eerst, anders substring).
    """
    locs = catalog.get("LocatieLijst", []) or []
    out = {}

    for wanted in wanted_names:
        w = wanted.strip().lower()

        # 1) exacte match op Naam
        exact = [l for l in locs if str(l.get("Naam", "")).strip().lower() == w]
        if exact:
            out[wanted] = exact[0]["Code"]
            continue

        # 2) substring match op Naam
        sub = [l for l in locs if w in str(l.get("Naam", "")).lower()]
        if sub:
            out[wanted] = sub[0]["Code"]
            continue

        # 3) substring match op Code
        subc = [l for l in locs if w.replace(" ", "") in str(l.get("Code", "")).lower().replace(".", "")]
        if subc:
            out[wanted] = subc[0]["Code"]
            continue

        out[wanted] = None

    return out

def has_any_waarnemingen(resp: dict) -> bool:
    """True if response contains at least 1 observation."""
    for wl in resp.get("WaarnemingenLijst", []) or []:
        if (wl.get("Waarnemingen") or []):
            return True
    return False


def find_waterhoogte_parameter_codes(catalog: dict) -> list[str]:
    """Return a de-duplicated list of parameter codes that look like waterhoogte (WATHTE)."""
    params = catalog.get("ParameterLijst", []) or []
    out: list[str] = []

    def looks_like_waterhoogte(p: dict) -> bool:
        txt = f"{p.get('Naam','')} {p.get('Omschrijving','')}".lower()
        if "waterhoogte" in txt or "waterstand" in txt:
            return True
        # Try common fields where the parameter references a Grootheid
        g = p.get("Grootheid") or {}
        if isinstance(g, dict) and (g.get("Code") == "WATHTE"):
            return True
        if p.get("GrootheidCode") == "WATHTE":
            return True
        return False

    for p in params:
        try:
            if looks_like_waterhoogte(p):
                code = p.get("Code")
                if code and code not in out:
                    out.append(code)
        except Exception:
            continue

    return out


def fetch_last_waterhoogte(location_code: str, start: datetime, end: datetime, proces_type: str | None = "meting") -> dict:
    """Fetch recent waterhoogte (WATHTE) for a location and return the raw OphalenWaarnemingen response."""
    aquo: dict = {
        "Compartiment": {"Code": "OW"},
        "Grootheid": {"Code": "WATHTE"},
    }
    if proces_type:
        aquo["ProcesType"] = proces_type

    payload = {
        "Locatie": {"Code": location_code},
        "AquoPlusWaarnemingMetadata": {
            "AquoMetadata": aquo,
        },
        "Periode": {
            "Begindatumtijd": iso(start),
            "Einddatumtijd": iso(end),
        },
    }
    return post_json(URL_OBS, payload)


def extract_parameter_code_from_last(resp: dict) -> str | None:
    """Try to extract a Parameter.Code from an OphalenLaatsteWaarnemingen response."""
    wlists = resp.get("WaarnemingenLijst", []) or []
    if not wlists:
        return None

    for wl in wlists:
        aquo = wl.get("AquoMetadata") or wl.get("AquoPlusWaarnemingMetadata", {}).get("AquoMetadata") or wl.get("AquoPlusWaarnemingMetadata", {}).get("AquoMetadata") or {}
        if isinstance(aquo, dict):
            p = aquo.get("Parameter")
            if isinstance(p, dict) and p.get("Code"):
                return p["Code"]

        waarn = wl.get("Waarnemingen") or []
        if isinstance(waarn, list) and waarn:
            first = waarn[0]
            if isinstance(first, dict):
                aquo2 = first.get("AquoMetadata") or {}
                if isinstance(aquo2, dict):
                    p2 = aquo2.get("Parameter")
                    if isinstance(p2, dict) and p2.get("Code"):
                        return p2["Code"]

    return None

def fetch_waterhoogte(
    location_code: str,
    start: datetime,
    end: datetime,
    proces_type: str | None,
    parameter_code: str | None = None,
):
    """
    Fetch waterhoogte for a location.

    If `parameter_code` is provided, we query by AquoMetadata.Parameter.Code.
    Otherwise we query by AquoMetadata.Grootheid.Code == WATHTE.

    proces_type examples:
      - None
      - "meting"
      - "verwachting"
    """
    aquo: dict = {
        "Compartiment": {"Code": "OW"},  # Oppervlaktewater
    }

    if parameter_code:
        aquo["Parameter"] = {"Code": parameter_code}
    else:
        aquo["Grootheid"] = {"Code": "WATHTE"}

    if proces_type:
        aquo["ProcesType"] = proces_type

    payload = {
        "Locatie": {"Code": location_code},
        "AquoPlusWaarnemingMetadata": {"AquoMetadata": aquo},
        "Periode": {
            "Begindatumtijd": iso(start),
            "Einddatumtijd": iso(end),
        },
    }
    return post_json(URL_OBS, payload)

def split_by_day(waarnemingen_lijsten: list, d0: date, d1: date, d2: date) -> dict:
    """
    Pakt alle Waarnemingen uit 1..n WaarnemingenLijsten en zet ze per dag in een dict.
    Verwacht dat elk meetpunt een tijdstempel en waarde heeft.
    """
    out = {
        d0.isoformat(): [],
        d1.isoformat(): [],
        d2.isoformat(): [],
    }

    if not waarnemingen_lijsten:
        return out

    for wl in waarnemingen_lijsten:
        for w in wl.get("Waarnemingen", []) or []:
            # Velden heten in de praktijk meestal "Datumtijd" en "Meetwaarde" (met subvelden)
            dt_raw = w.get("Datumtijd") or w.get("Tijdstip") or w.get("Datetime")
            mv = w.get("Meetwaarde", {}) or {}
            val = mv.get("Waarde_Numeriek", mv.get("Waarde_Alfanumeriek", None))

            if not dt_raw:
                continue

            try:
                dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00")).astimezone(TZ)
            except Exception:
                continue

            day = dt.date().isoformat()
            if day in out:
                out[day].append({"tijd": dt.isoformat(timespec="minutes"), "waarde": val})

    return out

def main():
    OUTPUT_FILE = Path("catalog.json")

    now = datetime.now(TZ)
    d_yesterday = (now - timedelta(days=1)).date()
    d_today = now.date()
    d_tomorrow = (now + timedelta(days=1)).date()

    start_yesterday = datetime.combine(d_yesterday, time(0, 0), TZ)
    end_tomorrow = datetime.combine(d_tomorrow, time(23, 59, 59), TZ)

    payload = {
        "CatalogusFilter": {
            "Compartimenten": True,
            "Grootheden": True,
            "Locaties": True,
            "Parameters": True,
            "Eenheden": True,
        }
    }
    # 1) Catalogus ophalen (voor locatiecodes)
    catalog = post_json(
        URL_CATALOG,
        {
            "CatalogusFilter": {
                "Compartimenten": True,
                "Grootheden": True,
                "Locaties": True,
                "ProcesTypes": True,
                "Parameters": True,
                "Eenheden": True,
            }
        },
    )
    loc_codes = find_location_codes(catalog, TARGETS)
    param_candidates = find_waterhoogte_parameter_codes(catalog)
    print(f"{len(param_candidates)} waterhoogte-parameter candidates found")
    print("Will prefer Parameter.Code discovered via last observation (sanity check) when available")
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(
            catalog,
            f,
            indent=2,
            ensure_ascii=False,
            sort_keys=True
        )
    print(f"{len(loc_codes)} locations found")
    print(f"{loc_codes}")
    missing = [k for k, v in loc_codes.items() if not v]
    if missing:
        print("Kon geen locatiecode vinden voor:", ", ".join(missing), file=sys.stderr)
        print("Tip: controleer de spelling of probeer een andere locatie-naam.", file=sys.stderr)

    result = {
        "periode": {
            "gisteren": d_yesterday.isoformat(),
            "vandaag": d_today.isoformat(),
            "morgen": d_tomorrow.isoformat(),
        },
        "locaties": {}
    }

    # 2) Data per locatie ophalen (meting + verwacht)
    for wanted_name, code in loc_codes.items():
        if not code:
            continue

        # --- Sanity check: what is the latest available waterhoogte for this location?
        last_param = None
        try:
            # sanity check window: last 2 days
            last_resp = fetch_last_waterhoogte(code, start=now - timedelta(days=2), end=now, proces_type="meting")
            last_param = extract_parameter_code_from_last(last_resp)

            if has_any_waarnemingen(last_resp):
                print(f"[INFO] Last waterhoogte meting exists for {wanted_name} ({code})")
            else:
                print(f"[WARN] No last waterhoogte meting found for {wanted_name} ({code})")
        except (requests.HTTPError, ValueError) as e:
            print(f"[WARN] Last observation check failed for {wanted_name} ({code}): {e}", file=sys.stderr)

        if last_param:
            print(f"[INFO] Sanity check chose Parameter.Code={last_param} for {wanted_name} ({code})")

        # --- Metingen: prefer Parameter.Code from sanity check, then Grootheid, then catalog parameter candidates
        obs_meet = {}

        # 1) Try sanity-check parameter first
        if last_param:
            try:
                obs_meet = fetch_waterhoogte(
                    code, start_yesterday, end_tomorrow, proces_type=None, parameter_code=last_param
                )
                if has_any_waarnemingen(obs_meet):
                    print(f"[INFO] Using sanity-check parameter {last_param} for meting at {wanted_name} ({code})")
            except (requests.HTTPError, ValueError) as e:
                print(f"[WARN] Sanity parameter meting fetch failed for {wanted_name} ({code}): {e}", file=sys.stderr)

        # 2) Try Grootheid queries
        if not has_any_waarnemingen(obs_meet):
            try:
                obs_meet = fetch_waterhoogte(code, start_yesterday, end_tomorrow, proces_type="meting")
                if not has_any_waarnemingen(obs_meet):
                    # Some locations don't use ProcesType filtering for measurements
                    obs_meet = fetch_waterhoogte(code, start_yesterday, end_tomorrow, proces_type=None)
            except (requests.HTTPError, ValueError) as e:
                print(f"[WARN] Meting request failed for {wanted_name} ({code}): {e}", file=sys.stderr)

        # 3) Fall back to parameter candidates from catalog
        if not has_any_waarnemingen(obs_meet) and param_candidates:
            for pc in param_candidates[:50]:  # safety cap
                try:
                    obs_meet = fetch_waterhoogte(code, start_yesterday, end_tomorrow, proces_type=None, parameter_code=pc)
                    if has_any_waarnemingen(obs_meet):
                        print(f"[INFO] Using parameter {pc} for meting at {wanted_name} ({code})")
                        break
                except (requests.HTTPError, ValueError):
                    continue

        # --- Verwachtingen: not every location has them
        obs_fore = {}

        # 1) Prefer sanity-check parameter (if we found one) with proces_type=verwachting
        if last_param:
            try:
                obs_fore = fetch_waterhoogte(
                    code, start_yesterday, end_tomorrow, proces_type="verwachting", parameter_code=last_param
                )
                if has_any_waarnemingen(obs_fore):
                    print(f"[INFO] Using sanity-check parameter {last_param} for verwachting at {wanted_name} ({code})")
            except (requests.HTTPError, ValueError) as e:
                print(f"[WARN] Sanity parameter verwachting fetch failed for {wanted_name} ({code}): {e}", file=sys.stderr)

        # 2) Try Grootheid-based verwachting
        if not has_any_waarnemingen(obs_fore):
            try:
                obs_fore = fetch_waterhoogte(code, start_yesterday, end_tomorrow, proces_type="verwachting")
            except (requests.HTTPError, ValueError) as e:
                print(f"[WARN] Geen verwachting voor {wanted_name} ({code}): {e}", file=sys.stderr)

        # 3) Fall back to parameter candidates
        if not has_any_waarnemingen(obs_fore) and param_candidates:
            for pc in param_candidates[:50]:  # safety cap
                try:
                    obs_fore = fetch_waterhoogte(code, start_yesterday, end_tomorrow, proces_type="verwachting", parameter_code=pc)
                    if has_any_waarnemingen(obs_fore):
                        print(f"[INFO] Using parameter {pc} for verwachting at {wanted_name} ({code})")
                        break
                except (requests.HTTPError, ValueError):
                    continue
        meet_split = split_by_day(obs_meet.get("WaarnemingenLijst", []), d_yesterday, d_today, d_tomorrow)
        fore_split = split_by_day(obs_fore.get("WaarnemingenLijst", []), d_yesterday, d_today, d_tomorrow)

        result["locaties"][wanted_name] = {
            "locatiecode": code,
            "meting": meet_split,
            "verwacht": fore_split,
        }

    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()