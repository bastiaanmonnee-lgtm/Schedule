"""Wedstrijden uit MatchDataOLAP.EventBase."""

from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta

import pandas as pd

LOCAL_TZ = "Europe/Amsterdam"

DEFAULT_CLUBS = ["Barcelona", "Real Madrid", "Juventus", "Manchester City", "FC Utrecht"]

QUERY = """
SELECT Id, StartDateTimeUtc, TournamentStageName,
       HomeTeamId, HomeTeam, HomeLogoId, AwayTeamId, AwayTeam, AwayLogoId
FROM MatchDataOLAP.EventBase
WHERE StartDateUtc BETWEEN ? AND ?
  AND ({team_filter})
ORDER BY StartDateTimeUtc
"""


def search_key(club: str) -> str:
    """'FC Utrecht' -> 'utrecht': zo vinden we ook 'Utrecht' of 'FC Utrecht' in de database."""
    key = club.strip().lower()
    for prefix in ("fc ", "afc ", "cf ", "sc "):
        key = key.removeprefix(prefix)
    return key.removesuffix(" fc").strip()


def fetch_matches(start: date, end: date, clubs: tuple[str, ...]) -> pd.DataFrame:
    """Alle wedstrijden in de periode waarbij een teamnaam een van de clubs bevat."""
    from db import connect  # pas importeren als er echt data opgehaald wordt

    patterns = [f"%{search_key(c)}%" for c in clubs if search_key(c)]
    # LOWER(): de database kan hoofdlettergevoelig zijn, de zoektermen zijn in kleine letters.
    team_filter = " OR ".join(["LOWER(HomeTeam) LIKE ? OR LOWER(AwayTeam) LIKE ?"] * len(patterns))
    # Een dag marge: UTC-datum en Nederlandse datum verschillen rond middernacht.
    params = (start - timedelta(days=1), end + timedelta(days=1), *[p for p in patterns for _ in range(2)])
    with closing(connect()) as conn:
        cursor = conn.cursor()
        cursor.execute(QUERY.format(team_filter=team_filter), params)
        columns = [c[0] for c in cursor.description]
        rows = [tuple(r) for r in cursor.fetchall()]
    return prepare(pd.DataFrame.from_records(rows, columns=columns), start, end)


def prepare(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    df = df.copy()
    df["aftrap"] = pd.to_datetime(df["StartDateTimeUtc"], utc=True).dt.tz_convert(LOCAL_TZ)
    df["datum"] = df["aftrap"].dt.date
    df["tijd"] = df["aftrap"].dt.strftime("%H:%M")
    return df[(df["datum"] >= start) & (df["datum"] <= end)].reset_index(drop=True)


def club_of(name: str, clubs: list[str]) -> str | None:
    """Bij welke gevolgde club hoort deze teamnaam?"""
    lowered = str(name).lower()
    return next((c for c in clubs if search_key(c) and search_key(c) in lowered), None)


def team_names(df: pd.DataFrame, clubs: list[str]) -> list[str]:
    names = pd.concat([df["HomeTeam"], df["AwayTeam"]]).dropna().unique()
    return sorted(n for n in names if club_of(n, clubs))


def default_teams(names: list[str], clubs: list[str]) -> list[str]:
    """Per club alleen de exacte naam (bijv. 'Utrecht' / 'FC Utrecht'), anders alles met die naam."""
    chosen = []
    for club in clubs:
        key = search_key(club)
        matching = [n for n in names if club_of(n, [club])]
        exact = [n for n in matching if n.lower() in {key, f"fc {key}", f"{key} fc", club.strip().lower()}]
        chosen += exact or matching
    return sorted(set(chosen))


def for_teams(df: pd.DataFrame, teams: list[str]) -> pd.DataFrame:
    """Alleen de wedstrijden van de gekozen teams; thuis_uit vanuit het gekozen team."""
    df = df[df["HomeTeam"].isin(teams) | df["AwayTeam"].isin(teams)].copy()
    df["thuis_uit"] = df["HomeTeam"].isin(teams).map({True: "Home", False: "Away"})
    return df.rename(columns={"TournamentStageName": "competitie"}).sort_values("aftrap")


def logo_candidates(row) -> tuple[list[int], list[int]]:
    """Mogelijke logo-id's voor thuis en uit: eerst de team-id, dan de logo-id."""
    def ids(*values):
        return [int(v) for v in values if pd.notna(v)]

    return (
        ids(row.HomeTeamId, row.HomeLogoId),
        ids(row.AwayTeamId, row.AwayLogoId),
    )
