"""Koppeling met Shiftbase (https://api.shiftbase.com/api). Voor nu alleen LEZEN: testen of de verbinding werkt.

De API-sleutel staat in .env als SHIFTBASE_API_KEY (aanmaken in Shiftbase: Settings > App center > Public API).

Testen (vanuit de projectmap):  python src/shiftbase.py
Wat staat er (koppeling maken):  python src/shiftbase.py discover
Lezen gebeurt met GET. Versturen (create_roster) maakt ALLEEN nieuwe diensten aan; undo (delete_roster) haalt
alleen diensten weg die deze tool zelf heeft aangemaakt (bijgehouden in data/shiftbase_sent.csv).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta

import config  # noqa: F401  (laadt .env)

BASE_URL = "https://api.shiftbase.com/api"
CONTENT_DEPARTMENT_ID = "133049"  # afdeling "Content (One Team)"
CONTENT_TEAM_ID = "193013"  # team "Content"


def _key() -> str:
    key = os.environ.get("SHIFTBASE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SHIFTBASE_API_KEY is not set in .env")
    return key


def _request(method: str, path: str, body: dict | None = None):
    url = f"{BASE_URL}/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"API {_key()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "433-Schedule/1.0 (+https://www.433.com)",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
            return resp.status, json.loads(text) if text else None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")[:500]


def create_roster(body: dict) -> str:
    """Maak één nieuwe dienst aan in Shiftbase. Geeft het nieuwe id terug; bij een fout een RuntimeError."""
    status, data = _request("POST", "rosters", body)
    if not 200 <= status < 300:
        raise RuntimeError(f"Shiftbase refused the shift (HTTP {status}): {data}")
    found = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(found, list) and found:
        found = found[0]
    roster = found.get("Roster", found) if isinstance(found, dict) else {}
    new_id = str(roster.get("id", "")) if isinstance(roster, dict) else ""
    if not new_id:
        raise RuntimeError(f"Shiftbase answered without an id: {str(data)[:300]}")
    return new_id


def delete_roster(roster_id: str) -> None:
    """Verwijder één dienst; alleen gebruikt voor diensten die deze tool zelf heeft aangemaakt (undo)."""
    status, data = _request("DELETE", f"rosters/{roster_id}")
    if not 200 <= status < 300:
        raise RuntimeError(f"Shiftbase could not delete shift {roster_id} (HTTP {status}): {data}")


def roster_ids(min_date: date, max_date: date) -> set[str] | None:
    """Id's van alle diensten die Shiftbase in deze periode laat zien (alle afdelingen). None bij een fout.

    Een dienst die in Shiftbase zelf is verwijderd, staat niet meer in deze lijst (los opvragen geeft hem nog wel)."""
    try:
        status, data = get("rosters", {"min_date": min_date.isoformat(), "max_date": max_date.isoformat()})
    except Exception:
        return None
    if status != 200 or not isinstance(data, dict):
        return None
    return {str(i["Roster"].get("id")) for i in data.get("data", [])
            if isinstance(i, dict) and isinstance(i.get("Roster"), dict)}


def get(path: str, params: dict | None = None):
    """GET-verzoek (alleen lezen). Geeft (statuscode, json of tekst) terug."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": f"API {_key()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        # Cloudflare bij Shiftbase blokkeert de standaard "Python-urllib"-naam (Error 1010).
        "User-Agent": "433-Schedule/1.0 (+https://www.433.com)",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body[:300]


def _count(data) -> str:
    """Kort overzicht van een antwoord, zonder persoonsgegevens te dumpen."""
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, list):
        return f"{len(data)} items"
    if isinstance(data, dict):
        return f"object with keys: {', '.join(list(data)[:8])}"
    return str(data)[:120]


def check_connection() -> None:
    monday = date.today() - timedelta(days=date.today().weekday())
    tests = [
        ("users", None),
        ("departments", None),
        ("teams", None),
        ("shifts", None),
        ("rosters", {"min_date": monday.isoformat(), "max_date": (monday + timedelta(days=6)).isoformat()}),
    ]
    print(f"Shiftbase connection test ({BASE_URL}), read-only\n")
    for path, params in tests:
        status, data = get(path, params)
        ok = "OK " if 200 <= status < 300 else "ERR"
        print(f"  [{ok}] GET /{path:<12} -> HTTP {status}: {_count(data) if ok == 'OK ' else data}")
    print("\nHTTP 200 = works. 401/403 = key wrong or no API access in your plan. 404 = endpoint name differs.")


# Onze diensten -> dienstsoort in Shiftbase (afdeling Content). De tijden van de tool gaan mee.
SHIFT_IDS = {"Day": "469047", "Evening": "927212", "Night": "942986"}  # DAYS, Even, NS
CHANNEL_NOTE = {"main": "Main", "app": "App", "nl": "NL"}


def existing_rosters(min_date: date, max_date: date) -> list[dict]:
    """Diensten die nu in Shiftbase staan voor het Content-team (alleen lezen)."""
    status, data = get("rosters", {"min_date": min_date.isoformat(), "max_date": max_date.isoformat()})
    if status != 200:
        raise RuntimeError(f"Shiftbase rosters: HTTP {status} {data}")
    raw = data.get("data", []) if isinstance(data, dict) else (data or [])
    result = []
    for item in raw:
        r = item.get("Roster") if isinstance(item, dict) else None
        if isinstance(r, dict) and str(r.get("department_id")) == CONTENT_DEPARTMENT_ID:
            result.append({"id": str(r.get("id")), "user_id": str(r.get("user_id")), "date": str(r.get("date")),
                           "shift_id": str(r.get("shift_id")), "starttime": str(r.get("starttime"))[:5],
                           "endtime": str(r.get("endtime"))[:5], "description": str(r.get("description") or "")})
    return result


def shift_breaks() -> dict[str, str]:
    """Pauze (minuten) per dienst-sjabloon in Shiftbase, bijv. {"469047": "30"}. Shiftbase eist dit veld."""
    status, data = get("shifts")
    if status != 200:
        return {}
    result = {}
    for item in (data.get("data", []) if isinstance(data, dict) else data or []):
        shift = item.get("Shift", item) if isinstance(item, dict) else None
        if isinstance(shift, dict) and shift.get("id") is not None:
            result[str(shift["id"])] = str(shift.get("break") or "0")
    return result


def payload(datum: date, dienst: str, kanaal: str, user_id: str, start: str, end: str,
            breaks: dict[str, str] | None = None) -> dict:
    """Hoe één dienst naar Shiftbase zou gaan (velden zoals in GET /rosters)."""
    shift_id = SHIFT_IDS.get(dienst, "")
    return {"Roster": {
        "department_id": CONTENT_DEPARTMENT_ID, "team_id": CONTENT_TEAM_ID, "shift_id": shift_id,
        "user_id": str(user_id), "date": datum.isoformat(),
        "starttime": f"{start}:00", "endtime": f"{end}:00", "break": (breaks or {}).get(shift_id, "0"),
        "description": CHANNEL_NOTE.get(kanaal, kanaal), "recurring": False,
    }}


def preview(planned, ids: dict[str, str], shift_times: dict[str, tuple[str, str]], min_date: date, max_date: date):
    """Vergelijk ons rooster met Shiftbase. Geeft per regel: wat we zouden sturen en of het er al staat.

    Er wordt NIETS verstuurd."""
    import pandas as pd

    there = existing_rosters(min_date, max_date)
    breaks = shift_breaks()
    by_key = {(r["user_id"], r["date"]): r for r in there}
    ours = set()
    rows = []
    for r in planned.sort_values(["datum", "dienst"]).itertuples():
        uid = ids.get(r.naam, "")
        start, end = shift_times.get(r.dienst, ("", ""))
        ours.add((uid, r.datum.isoformat()))
        now = by_key.get((uid, r.datum.isoformat()))
        if not uid:
            status = "⚠ no Shiftbase ID"
        elif not SHIFT_IDS.get(r.dienst):
            status = "⚠ no Shiftbase shift"
        elif now is None:
            status = "new"
        elif now["shift_id"] == SHIFT_IDS[r.dienst] and now["starttime"] == start:
            status = "already in Shiftbase"
        else:
            status = f"different in Shiftbase ({now['starttime']}–{now['endtime']})"
        rows.append({"Date": r.datum, "Name": r.naam, "Shift": f"{r.dienst} {start}–{end}",
                     "Channel": CHANNEL_NOTE.get(r.kanaal, r.kanaal), "Shiftbase ID": uid, "Status": status,
                     "_body": payload(r.datum, r.dienst, r.kanaal, uid, start, end, breaks) if status == "new" else None})
    team_ids = set(ids.values())
    for r in there:  # staat in Shiftbase, maar niet in ons rooster (voor onze mensen)
        if r["user_id"] in team_ids and (r["user_id"], r["date"]) not in ours:
            name = next((n for n, i in ids.items() if i == r["user_id"]), r["user_id"])
            rows.append({"Date": date.fromisoformat(r["date"]), "Name": name, "Shift": f"{r['starttime']}–{r['endtime']}",
                         "Channel": r["description"], "Shiftbase ID": r["user_id"], "Status": "only in Shiftbase",
                         "_body": None})
    return pd.DataFrame(rows, columns=["Date", "Name", "Shift", "Channel", "Shiftbase ID", "Status", "_body"]).sort_values(
        ["Date", "Name"]).reset_index(drop=True)


def items(data) -> list[dict]:
    """Lijst uit een antwoord; Shiftbase verpakt elk item soms per model, bijv. {"User": {...}}."""
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        data = [data]
    result = []
    for item in data or []:
        if isinstance(item, dict) and len(item) >= 1 and all(isinstance(v, (dict, list)) for v in item.values()):
            main = next((v for v in item.values() if isinstance(v, dict)), item)
            result.append({**main, "_wrapped": list(item)})
        else:
            result.append(item)
    return result


def _pick(item: dict, *names):
    return next((item[n] for n in names if n in item and item[n] not in (None, "")), "")


def discover(employee_names: list[str]) -> None:
    """Alleen lezen: wat staat er in Shiftbase, zodat we de koppeling kunnen maken."""
    def show(path, fields):
        status, data = get(path)
        rows = items(data) if status == 200 else []
        print(f"\n== {path} (HTTP {status}, {len(rows)} items) ==")
        if rows:
            print("   fields:", ", ".join(k for k in rows[0] if not k.startswith("_")))
        for r in rows:
            print("  ", " | ".join(f"{f}={_pick(r, f)}" for f in fields if _pick(r, f) != ""))
        return rows

    show("departments", ["id", "name"])
    show("teams", ["id", "name", "department_id"])
    show("shifts", ["id", "name", "long_name", "starttime", "endtime", "department_id"])

    status, data = get("users")
    users = items(data) if status == 200 else []
    wanted = {n.lower() for n in employee_names}
    print(f"\n== users (HTTP {status}): matching the {len(employee_names)} employees in the tool ==")
    if users:
        print("   fields:", ", ".join(k for k in users[0] if not k.startswith("_")))
    found = set()
    for u in users:
        first = str(_pick(u, "first_name", "firstname")).strip()
        full = str(_pick(u, "name", "display_name")).strip() or f"{first} {_pick(u, 'last_name', 'lastname')}".strip()
        if first.lower() in wanted or full.split(" ")[0].lower() in wanted:
            found.add(first.lower() or full.split(" ")[0].lower())
            print(f"   id={_pick(u, 'id')} | name={full} | active={_pick(u, 'active', 'enabled')}")
    missing = sorted(wanted - found)
    if missing:
        print("   not found by first name:", ", ".join(missing))

    monday = date.today() - timedelta(days=date.today().weekday())
    status, data = get("rosters", {"min_date": monday.isoformat(), "max_date": (monday + timedelta(days=6)).isoformat()})
    rosters = items(data) if status == 200 else []
    print(f"\n== rosters this week (HTTP {status}): structure of one item, without its values ==")
    if rosters:
        print("   wrapped as:", rosters[0].get("_wrapped"))
        print("   fields:", ", ".join(k for k in rosters[0] if not k.startswith("_")))
    raw = data.get("data", []) if isinstance(data, dict) else (data or [])
    content = [r for r in raw if isinstance(r, dict) and isinstance(r.get("Roster"), dict)
               and str(r["Roster"].get("team_id")) == CONTENT_TEAM_ID]
    sample = (content or [r for r in raw if isinstance(r, dict) and isinstance(r.get("Roster"), dict)])[:1]
    if sample:
        roster = sample[0]["Roster"]
        print("\n== Roster fields (one example, ids/dates/times only) ==")
        safe = ("id", "date", "starttime", "endtime", "shift_id", "team_id", "department_id", "user_id",
                "break", "published", "type", "occurrence_id", "recurring", "hide_end_time")
        for k, v in roster.items():
            print(f"   {k} = {v if k in safe or k.endswith('_id') else '(…)'}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "discover":
        import storage

        discover(list(storage.load_team()["naam"]))
    else:
        check_connection()
