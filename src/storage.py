"""Opslag van alle tabellen als CSV in de map data/."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

# Standaard de map data/ in de projectroot; in Docker gemount als volume.
DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent / "data")

DEFAULT_SETTINGS = {
    "min_rust_uren": 11,
}

CHANNELS = ["main", "app", "nl"]

TEAM_COLUMNS = [
    "naam", "shiftbase_id", "seniority", "kanalen", "contracturen", "nuluren", "auto", "geen_avond", "avond_vanaf", "avond_samen", "max_per_week", "vrije_dagen",
    "vaste_dienst", "vaste_dagen", "ucl", "actief", "notitie",
]
_EMPLOYEES = ["Karel", "Jonathan", "Luuk", "Kadir", "Francisco", "Joshua", "Thijn",
              "Justin", "Maybel*", "Tim", "Rogier", "Mats", "Thomas*", "Arodi*"]
_ONLY = {"Kadir": "app", "Francisco": "app", "Luuk": "app", "Jonathan": "main"}
_MANUAL_ONLY = {"Rogier", "Tim"}  # niet automatisch inroosteren
# Nummer van de medewerker in Shiftbase (voor het versturen van het rooster). Arodi: het nieuwste account.
SHIFTBASE_IDS = {
    "Aniek": "760456", "Emily": "1592456", "Francisco": "997192", "Jonathan": "1000286", "Joshua": "1494199",
    "Justin": "1332930", "Kadir": "870467", "Karel": "1388199", "Luuk": "1494201", "Mats": "1433271",
    "Maybel": "1568624", "Rogier": "774750", "Thijn": "1332929", "Thomas": "760457", "Tim": "760510",
    "Arodi": "1604152",
}
DEFAULT_TEAM = pd.DataFrame(
    [
        {
            "naam": n.rstrip("*"), "shiftbase_id": SHIFTBASE_IDS.get(n.rstrip("*"), ""), "seniority": "Intern" if n.rstrip("*") == "Maybel" else "Medior", "kanalen": _ONLY.get(n, "main, app, nl"), "auto": n not in _MANUAL_ONLY, "geen_avond": False, "avond_vanaf": "", "avond_samen": False, "nuluren": False,
            "contracturen": 16 if n == "Thijn" else 40, "max_per_week": 5, "vrije_dagen": "",
            # Francisco werkt altijd 16:00 - 00:00 en steevast op vrijdag; Rogier staat bij UCL op main.
            "vaste_dienst": "Evening" if n == "Francisco" else "", "vaste_dagen": "fri" if n == "Francisco" else "",
            "ucl": n == "Rogier", "actief": True, "notitie": "*" if n.endswith("*") else "",
        }
        for n in _EMPLOYEES
    ],
    columns=TEAM_COLUMNS,
)

# Per maand bij een ander team (bijv. WomenFC): die maand niet in dit rooster.
DEFAULT_OTHER_TEAM = pd.DataFrame(
    [],
    columns=["naam", "maand", "team"],
)

# Regels per persoon; de soorten staan in scheduler.RULE_TYPES.
DEFAULT_PERSON_RULES = pd.DataFrame(
    [
        ("Karel", "Only on channel", "main"),
        ("Karel", "Prefers shift", "Day"),
        ("Jonathan", "Prefers shift", "Evening"),
        ("Jonathan", "Always works", "Day: mon, fri"),
    ],
    columns=["naam", "regel", "waarde"],
)

# Extra bezetting bij wedstrijden en events; de aanleidingen staan in scheduler.TRIGGERS.
DEFAULT_MATCH_STAFFING = pd.DataFrame(
    [
        ("Team plays", "Netherlands", 0, 0, 1),
        ("Big match", "", 1, 1, 0),
        ("Special event", "", 1, 1, 0),
    ],
    columns=["aanleiding", "waarde", "main", "app", "nl"],
)

DEFAULT_SHIFTS = pd.DataFrame(
    # Night: alleen handmatig (bijv. bij wedstrijden na middernacht); de planner vult hem niet.
    [("Day", "09:00", "17:00"), ("Evening", "16:00", "00:00"), ("Night", "00:00", "09:00")],
    columns=["dienst", "start", "eind"],
)

_RULES = {
    # dagtype: {dienst: (main, app, nl)}
    "Regular day": {"Day": (1, 1, 1), "Evening": (1, 1, 1)},
    "Weekend": {"Day": (1, 1, 1), "Evening": (1, 1, 1)},  # zoals in het oude rooster: ±3 per weekendavond (+ extra bij topduels)
    "Champions League": {"Day": (1, 1, 1), "Evening": (2, 2, 2)},  # vast 2 per kanaal; geen extra's erbovenop
}
DEFAULT_RULES = pd.DataFrame(
    [(dt, d, *counts) for dt, shifts in _RULES.items() for d, counts in shifts.items()],
    columns=["dagtype", "dienst", *CHANNELS],
)

EMPTY = {
    "days": ["datum", "handmatig", "notitie"],
    "afwezigheid": ["naam", "van", "tot", "deel", "reden"],  # deel: "" = hele dag, "Day" of "Evening"
    "schedule": ["datum", "dienst", "kanaal", "naam"],
    "events": ["titel", "datum"],
}


def _path(name: str) -> Path:
    return DATA_DIR / f"{name}.csv"


def _read(name: str, default: pd.DataFrame) -> pd.DataFrame:
    path = _path(name)
    if not path.exists():
        return default.copy()
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _write(name: str, df: pd.DataFrame) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(_path(name), index=False)


def _text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date


def _ints(series: pd.Series, default: int = 0) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default).astype(int)


def _bools(series: pd.Series) -> pd.Series:
    return series.map(lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "1", "ja"))


# --- opschonen (ook gebruikt op output van st.data_editor) ---------------------------

def channel_list(value) -> list[str]:
    """'main, app' (of een lijst uit de multiselect) -> ['main', 'app']"""
    parts = value if isinstance(value, (list, tuple)) else str(value or "").replace(";", ",").split(",")
    chosen = {str(p).strip().lower() for p in parts}
    return [c for c in CHANNELS if c in chosen]


def clean_team(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in TEAM_COLUMNS:
        if col not in df:
            # Nieuwe kolom in een bestaand bestand: de standaardwaarde per naam, anders een algemene.
            fallback = True if col == "auto" else False if col in ("geen_avond", "nuluren", "avond_samen") else DEFAULT_TEAM[col].iloc[0] if col in ("seniority", "contracturen", "max_per_week", "actief") else ""
            df[col] = df["naam"].map(dict(zip(DEFAULT_TEAM["naam"], DEFAULT_TEAM[col]))).fillna(fallback) if "naam" in df else fallback
    df["naam"] = _text(df["naam"])
    df["shiftbase_id"] = _text(df["shiftbase_id"]).str.replace(r"\.0$", "", regex=True)
    df["shiftbase_id"] = df["shiftbase_id"].where(df["shiftbase_id"] != "", df["naam"].map(SHIFTBASE_IDS).fillna(""))
    df["seniority"] = _text(df["seniority"]).replace("", "Medior")
    df["kanalen"] = df["kanalen"].map(lambda v: ", ".join(channel_list(v)))
    df["contracturen"] = _ints(df["contracturen"], 0)
    df["max_per_week"] = _ints(df["max_per_week"], 5)
    df["vrije_dagen"] = _text(df["vrije_dagen"])
    df["vaste_dienst"] = _text(df["vaste_dienst"])
    df["vaste_dagen"] = _text(df["vaste_dagen"])
    df["ucl"] = _bools(df["ucl"])
    df["auto"] = _bools(df["auto"].fillna(True).replace("", True))  # lege cel = aan
    df["geen_avond"] = _bools(df["geen_avond"].fillna(False))
    df["nuluren"] = _bools(df["nuluren"].fillna(False))
    df["avond_samen"] = _bools(df["avond_samen"].fillna(False))
    df["avond_vanaf"] = _dates(df["avond_vanaf"]).map(lambda d: d.isoformat() if pd.notna(d) else "")
    df["actief"] = _bools(df["actief"].fillna(True).replace("", True))  # lege cel = aan
    df["notitie"] = _text(df["notitie"])
    df = df[df["naam"] != ""].drop_duplicates("naam")
    return df[TEAM_COLUMNS].reset_index(drop=True)


def clean_shifts(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in df.columns:
        df[col] = _text(df[col])
    valid = df["start"].str.fullmatch(r"\d{1,2}:\d{2}") & df["eind"].str.fullmatch(r"\d{1,2}:\d{2}")
    df["dienst"] = (df["dienst"])
    df = df[(df["dienst"] != "") & valid].drop_duplicates("dienst")
    return df.reset_index(drop=True)


def clean_rules(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["dagtype"] = _text(df["dagtype"])
    df["dienst"] = _text(df["dienst"])
    for channel in CHANNELS:
        df[channel] = _ints(df[channel])
    df = df[(df["dagtype"] != "") & (df["dienst"] != "")].drop_duplicates(["dagtype", "dienst"], keep="last")
    return df.reset_index(drop=True)


def clean_days(df: pd.DataFrame) -> pd.DataFrame:
    """Per datum een handmatig gekozen dagtype (leeg = automatisch) en een notitie."""
    df = df.copy()
    df["datum"] = _dates(df["datum"])
    df["handmatig"] = _text(df["handmatig"])
    df["notitie"] = _text(df["notitie"])
    return df.dropna(subset=["datum"]).drop_duplicates("datum", keep="last").reset_index(drop=True)


def clean_absences(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["naam"] = _text(df["naam"])
    df["van"] = _dates(df["van"])
    df["tot"] = _dates(df["tot"])
    df["tot"] = df["tot"].where(df["tot"].notna(), df["van"])
    df["deel"] = _text(df["deel"]) if "deel" in df else ""
    df["deel"] = df["deel"].replace({"Whole day": "", "All day": "", "Day off": "Day", "Evening off": "Evening"})
    df["reden"] = _text(df["reden"]) if "reden" in df else ""
    return df[(df["naam"] != "")].dropna(subset=["van"])[EMPTY["afwezigheid"]].reset_index(drop=True)


def clean_events(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["titel"] = _text(df["titel"])
    df["datum"] = _dates(df["datum"])
    df = df[df["titel"] != ""].dropna(subset=["datum"])
    return df.sort_values("datum").reset_index(drop=True)


def clean_other_team(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["naam"] = _text(df["naam"])
    df["maand"] = _text(df["maand"])
    df["team"] = _text(df["team"]).replace("", "WomenFC")
    df = df[(df["naam"] != "") & df["maand"].str.fullmatch(r"\d{4}-\d{2}")]
    return df.drop_duplicates(["naam", "maand"], keep="last").sort_values(["maand", "naam"]).reset_index(drop=True)


def other_team_as_absences(df: pd.DataFrame) -> pd.DataFrame:
    """Een maand bij een ander team telt voor de planning als afwezig, de hele maand."""
    first = pd.to_datetime(df["maand"] + "-01")
    return pd.DataFrame({
        "naam": df["naam"], "van": first.dt.date, "tot": (first + pd.offsets.MonthEnd(0)).dt.date, "deel": "",
        "reden": df["team"],
    }, columns=EMPTY["afwezigheid"])


def clean_person_rules(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("naam", "regel", "waarde"):
        df[col] = _text(df[col])
    # Kanalen in kleine letters ('NL' -> 'nl'), zoals in het rooster.
    channel = df["regel"].isin(["Never alone on channel", "Only on channel"])
    df.loc[channel, "waarde"] = df.loc[channel, "waarde"].str.lower()
    df = df[(df["naam"] != "") & (df["regel"] != "") & (df["waarde"] != "")]
    return df.drop_duplicates().reset_index(drop=True)


def clean_match_staffing(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["aanleiding"] = _text(df["aanleiding"])
    df["waarde"] = _text(df["waarde"])
    for channel in CHANNELS:
        df[channel] = _ints(df[channel])
    return df[df["aanleiding"] != ""].reset_index(drop=True)


def clean_zero_hours(df: pd.DataFrame) -> pd.DataFrame:
    """Nuluren: per persoon de dagen (en eventueel dienst) waarop hij werkt."""
    df = df.copy()
    df["naam"] = _text(df["naam"])
    df["datum"] = _dates(df["datum"])
    df["dienst"] = _text(df["dienst"]).replace("", "Any")
    df = df[df["naam"] != ""].dropna(subset=["datum"]).drop_duplicates(["naam", "datum"], keep="last")
    return df.sort_values(["datum", "naam"]).reset_index(drop=True)


def clean_roster(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["datum"] = _dates(df["datum"])
    df["dienst"] = _text(df["dienst"])
    df["kanaal"] = _text(df["kanaal"]).str.lower()
    df["naam"] = _text(df["naam"])
    df = df.dropna(subset=["datum"])
    df = df[(df["dienst"] != "") & (df["naam"] != "") & df["kanaal"].isin(CHANNELS)]
    return df[EMPTY["schedule"]].sort_values(["datum", "dienst", "kanaal", "naam"]).reset_index(drop=True)


# --- laden / opslaan -----------------------------------------------------------------

def load_team():
    return clean_team(_read("employees", DEFAULT_TEAM))


def load_shifts():
    shifts = clean_shifts(_read("shifts", DEFAULT_SHIFTS))
    missing = DEFAULT_SHIFTS[~DEFAULT_SHIFTS["dienst"].isin(shifts["dienst"])]  # nieuwe standaarddiensten (Night)
    return pd.concat([shifts, missing], ignore_index=True)


def load_rules():
    return clean_rules(_read("staffing", DEFAULT_RULES))


def load_days():
    return clean_days(_read("days", pd.DataFrame(columns=EMPTY["days"])))


def load_absences():
    return clean_absences(_read("afwezigheid", pd.DataFrame(columns=EMPTY["afwezigheid"])))


def load_events():
    return clean_events(_read("events", pd.DataFrame(columns=EMPTY["events"])))


def load_other_team():
    return clean_other_team(_read("other_team", DEFAULT_OTHER_TEAM))


def load_person_rules():
    return clean_person_rules(_read("person_rules", DEFAULT_PERSON_RULES))


def load_match_staffing():
    return clean_match_staffing(_read("match_staffing", DEFAULT_MATCH_STAFFING))


def load_zero_hours():
    return clean_zero_hours(_read("zero_hours", pd.DataFrame(columns=["naam", "datum", "dienst"])))


SENT_COLUMNS = ["shiftbase_id", "datum", "naam", "dienst", "kanaal", "verstuurd"]


def load_sent() -> pd.DataFrame:
    """Diensten die deze tool zelf naar Shiftbase heeft gestuurd (alleen die mogen via Undo weg)."""
    df = _read("shiftbase_sent", pd.DataFrame(columns=SENT_COLUMNS))
    for col in SENT_COLUMNS:
        if col not in df:
            df[col] = ""
    df["datum"] = _dates(df["datum"])
    return df[SENT_COLUMNS]


def save_sent(df: pd.DataFrame) -> None:
    _write("shiftbase_sent", df[SENT_COLUMNS])


def load_roster():
    return clean_roster(_read("schedule", pd.DataFrame(columns=EMPTY["schedule"])))


def save(name: str, df: pd.DataFrame) -> None:
    _write(name, df)


def load_settings() -> dict:
    path = DATA_DIR / "instellingen.json"
    settings = dict(DEFAULT_SETTINGS)
    if path.exists():
        settings.update(json.loads(path.read_text(encoding="utf-8")))
    return settings


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "instellingen.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
