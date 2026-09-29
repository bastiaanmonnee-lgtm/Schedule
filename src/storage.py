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
    "standaard_doordeweeks": "Regular day",
    "standaard_weekend": "Eredivisie matchday",
}

DEFAULT_TEAM = pd.DataFrame(
    [
        ("Example Lead", "Lead", 4, "", True, ""),
        ("Example Senior 1", "Senior", 5, "", True, ""),
        ("Example Senior 2", "Senior", 5, "", True, ""),
        ("Example Medior 1", "Medior", 5, "", True, ""),
        ("Example Medior 2", "Medior", 5, "", True, ""),
        ("Example Medior 3", "Medior", 4, "fri", True, "Part-time"),
        ("Example Junior 1", "Junior", 5, "", True, ""),
        ("Example Junior 2", "Junior", 3, "mon, tue", True, "Intern"),
    ],
    columns=["naam", "seniority", "max_per_week", "vrije_dagen", "actief", "notitie"],
)

DEFAULT_SHIFTS = pd.DataFrame(
    [("Morning", "07:00", "15:00"), ("Afternoon", "13:00", "21:00"), ("Evening", "17:00", "01:00")],
    columns=["dienst", "start", "eind"],
)

_RULES = {
    # dagtype: {dienst: (aantal, waarvan minimaal senior)}
    "Regular day": {"Morning": (1, 0), "Afternoon": (1, 0), "Evening": (1, 0)},
    "Quiet / no football": {"Morning": (1, 0), "Afternoon": (1, 0)},
    "Eredivisie matchday": {"Morning": (1, 0), "Afternoon": (2, 1), "Evening": (2, 1)},
    "Big match / derby": {"Morning": (1, 0), "Afternoon": (2, 1), "Evening": (3, 2)},
    "Champions League": {"Morning": (1, 0), "Afternoon": (2, 1), "Evening": (4, 2)},
    "Europa / Conference League": {"Morning": (1, 0), "Afternoon": (1, 0), "Evening": (3, 1)},
    "International break": {"Morning": (1, 0), "Afternoon": (1, 0), "Evening": (2, 1)},
    "Transfer Deadline Day": {"Morning": (2, 1), "Afternoon": (2, 1), "Evening": (3, 1)},
}
DEFAULT_RULES = pd.DataFrame(
    [(dt, d, n, s) for dt, shifts in _RULES.items() for d, (n, s) in shifts.items()],
    columns=["dagtype", "dienst", "aantal", "min_senior"],
)

# Oude Nederlandse standaardnamen in bestaande data worden bij het laden omgezet naar het Engels.
_RENAMES = {
    "Normale dag": "Regular day",
    "Rustig / geen voetbal": "Quiet / no football",
    "Eredivisie speeldag": "Eredivisie matchday",
    "Klassieker / topduel": "Big match / derby",
    "Interland": "International break",
    "Ochtend": "Morning",
    "Middag": "Afternoon",
    "Avond": "Evening",
}


def _english(series: pd.Series) -> pd.Series:
    return series.replace(_RENAMES)


EMPTY = {
    "kalender": ["datum", "dagtype", "notitie"],
    "afwezigheid": ["naam", "van", "tot", "reden"],
    "rooster": ["datum", "dienst", "naam"],
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

def clean_team(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["naam"] = _text(df["naam"])
    df["seniority"] = _text(df["seniority"]).replace("", "Medior")
    df["max_per_week"] = _ints(df["max_per_week"], 5)
    df["vrije_dagen"] = _text(df["vrije_dagen"])
    df["actief"] = _bools(df["actief"].fillna(True))
    df["notitie"] = _text(df["notitie"])
    df = df[df["naam"] != ""].drop_duplicates("naam")
    return df.reset_index(drop=True)


def clean_shifts(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in df.columns:
        df[col] = _text(df[col])
    valid = df["start"].str.fullmatch(r"\d{1,2}:\d{2}") & df["eind"].str.fullmatch(r"\d{1,2}:\d{2}")
    df["dienst"] = _english(df["dienst"])
    df = df[(df["dienst"] != "") & valid].drop_duplicates("dienst")
    return df.reset_index(drop=True)


def clean_rules(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["dagtype"] = _english(_text(df["dagtype"]))
    df["dienst"] = _english(_text(df["dienst"]))
    df["aantal"] = _ints(df["aantal"])
    df["min_senior"] = _ints(df["min_senior"])
    df = df[(df["dagtype"] != "") & (df["dienst"] != "")].drop_duplicates(["dagtype", "dienst"], keep="last")
    return df.reset_index(drop=True)


def clean_calendar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["datum"] = _dates(df["datum"])
    df["dagtype"] = _english(_text(df["dagtype"]))
    df["notitie"] = _text(df["notitie"])
    return df.dropna(subset=["datum"]).drop_duplicates("datum", keep="last").reset_index(drop=True)


def clean_absences(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["naam"] = _text(df["naam"])
    df["van"] = _dates(df["van"])
    df["tot"] = _dates(df["tot"])
    df["tot"] = df["tot"].where(df["tot"].notna(), df["van"])
    df["reden"] = _text(df["reden"])
    return df[(df["naam"] != "")].dropna(subset=["van"]).reset_index(drop=True)


def clean_roster(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["datum"] = _dates(df["datum"])
    df["dienst"] = _english(_text(df["dienst"]))
    df["naam"] = _text(df["naam"])
    df = df.dropna(subset=["datum"])
    df = df[(df["dienst"] != "") & (df["naam"] != "")]
    return df.sort_values(["datum", "dienst", "naam"]).reset_index(drop=True)


# --- laden / opslaan -----------------------------------------------------------------

def load_team():
    return clean_team(_read("team", DEFAULT_TEAM))


def load_shifts():
    return clean_shifts(_read("diensten", DEFAULT_SHIFTS))


def load_rules():
    return clean_rules(_read("regels", DEFAULT_RULES))


def load_calendar():
    return clean_calendar(_read("kalender", pd.DataFrame(columns=EMPTY["kalender"])))


def load_absences():
    return clean_absences(_read("afwezigheid", pd.DataFrame(columns=EMPTY["afwezigheid"])))


def load_roster():
    return clean_roster(_read("rooster", pd.DataFrame(columns=EMPTY["rooster"])))


def save(name: str, df: pd.DataFrame) -> None:
    _write(name, df)


def load_settings() -> dict:
    path = DATA_DIR / "instellingen.json"
    settings = dict(DEFAULT_SETTINGS)
    if path.exists():
        settings.update(json.loads(path.read_text(encoding="utf-8")))
    for key in ("standaard_doordeweeks", "standaard_weekend"):
        settings[key] = _RENAMES.get(settings[key], settings[key])
    return settings


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "instellingen.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
