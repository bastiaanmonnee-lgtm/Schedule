"""Roosterlogica: automatisch inroosteren en controleren van bezetting."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

import pandas as pd

SENIORITY_LEVELS = ["Junior", "Medior", "Senior", "Lead"]
SENIOR_LEVELS = {"Senior", "Lead"}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# Vaste vrije dagen mogen in het Engels of Nederlands ("mon, wed" of "ma, wo").
_DAY_PREFIXES = [("mo", "ma"), ("tu", "di"), ("we", "wo"), ("th", "do"), ("fr", "vr"), ("sa", "za"), ("su", "zo")]

STATUS_OK = "OK"
STATUS_SHORT = "Understaffed"
STATUS_NO_SENIOR = "Too few seniors"
STATUS_OVER = "Overstaffed"


def is_senior(level: str) -> bool:
    return level in SENIOR_LEVELS


def parse_time(value: str) -> time:
    hours, minutes = str(value).strip().split(":")
    return time(int(hours), int(minutes))


def shift_window(day: date, start: str, end: str) -> tuple[datetime, datetime]:
    """Start- en eindmoment van een dienst; eindtijd <= starttijd betekent over middernacht."""
    s = datetime.combine(day, parse_time(start))
    e = datetime.combine(day, parse_time(end))
    if e <= s:
        e += timedelta(days=1)
    return s, e


def parse_free_days(value) -> set[int]:
    """'mon, wed' (of 'ma, wo') -> {0, 2}"""
    if not isinstance(value, str):
        return set()
    days = set()
    for part in value.replace(";", ",").replace("/", ",").split(","):
        key = part.strip().lower()[:2]
        for index, prefixes in enumerate(_DAY_PREFIXES):
            if key in prefixes:
                days.add(index)
    return days


def week_key(day: date) -> tuple[int, int]:
    iso = day.isocalendar()
    return iso[0], iso[1]


def _absence_map(absences: pd.DataFrame) -> dict[str, list[tuple[date, date]]]:
    result: dict[str, list[tuple[date, date]]] = defaultdict(list)
    for row in absences.itertuples():
        start, end = row.van, row.tot if pd.notna(row.tot) else row.van
        result[row.naam].append((min(start, end), max(start, end)))
    return result


def _is_absent(name: str, day: date, absence_map) -> bool:
    return any(start <= day <= end for start, end in absence_map.get(name, []))


def _rest_ok(windows, start: datetime, end: datetime, min_rest: timedelta) -> bool:
    for s, e in windows:
        if start >= e:
            gap = start - e
        elif s >= end:
            gap = s - end
        else:
            return False  # overlap
        if gap < min_rest:
            return False
    return True


def slots_for_day(dagtype: str, rules: pd.DataFrame, shift_order: list[str]) -> list[dict]:
    day_rules = rules[rules["dagtype"] == dagtype]
    slots = []
    for r in day_rules.itertuples():
        if r.dienst not in shift_order or int(r.aantal) <= 0:
            continue
        slots.append(
            {
                "dienst": r.dienst,
                "aantal": int(r.aantal),
                "min_senior": min(int(r.min_senior), int(r.aantal)),
                "filled": [],
            }
        )
    slots.sort(key=lambda s: shift_order.index(s["dienst"]))
    return slots


def generate_schedule(
    team: pd.DataFrame,
    shifts: pd.DataFrame,
    rules: pd.DataFrame,
    calendar: pd.DataFrame,
    absences: pd.DataFrame,
    min_rest_hours: float = 11,
    seed: int = 0,
) -> pd.DataFrame:
    """Maak een roostervoorstel.

    Per dag worden eerst de verplichte senior-plekken gevuld, daarna de overige plekken.
    Wie het minst heeft gewerkt (naar verhouding van zijn max per week) gaat voor.
    Geeft de toewijzingen terug (datum, dienst, naam); tekorten toont check_coverage.
    """
    rng = random.Random(seed)
    min_rest = timedelta(hours=min_rest_hours)
    shift_times = {r.dienst: (r.start, r.eind) for r in shifts.itertuples()}
    shift_order = list(shift_times)
    absence_map = _absence_map(absences)

    people = {
        r.naam: {
            "level": r.seniority,
            "max_week": int(r.max_per_week),
            "free": parse_free_days(r.vrije_dagen),
        }
        for r in team[team["actief"]].itertuples()
    }

    total = Counter()
    per_week = Counter()
    per_shift = Counter()
    windows: dict[str, list] = defaultdict(list)
    worked_days: dict[str, set] = defaultdict(set)
    assignments = []

    def eligible(name: str, day: date, dienst: str) -> bool:
        info = people[name]
        if day.weekday() in info["free"] or day in worked_days[name]:
            return False
        if per_week[(name, week_key(day))] >= info["max_week"]:
            return False
        if _is_absent(name, day, absence_map):
            return False
        start, end = shift_window(day, *shift_times[dienst])
        return _rest_ok(windows[name], start, end, min_rest)

    def pick(day: date, slot: dict, senior_only: bool) -> str | None:
        candidates = [
            n
            for n in people
            if n not in slot["filled"]
            and (not senior_only or is_senior(people[n]["level"]))
            and eligible(n, day, slot["dienst"])
        ]
        if not candidates:
            return None

        def score(n):
            cap = max(people[n]["max_week"], 1)
            return (
                round(total[n] / cap, 3),
                per_week[(n, week_key(day))] / cap,
                per_shift[(n, slot["dienst"])],
                # Spaar seniors op voor plekken waar ze echt nodig zijn.
                1 if (not senior_only and is_senior(people[n]["level"])) else 0,
                rng.random(),
            )

        return min(candidates, key=score)

    for row in calendar.sort_values("datum").itertuples():
        day = row.datum
        slots = slots_for_day(row.dagtype, rules, shift_order)

        for senior_pass in (True, False):
            order = sorted(slots, key=lambda s: -s["min_senior"]) if senior_pass else slots
            for slot in order:
                if senior_pass:
                    have = sum(is_senior(people[n]["level"]) for n in slot["filled"])
                    need = slot["min_senior"] - have
                else:
                    need = slot["aantal"] - len(slot["filled"])
                for _ in range(max(need, 0)):
                    name = pick(day, slot, senior_only=senior_pass)
                    if name is None:
                        break
                    slot["filled"].append(name)
                    total[name] += 1
                    per_week[(name, week_key(day))] += 1
                    per_shift[(name, slot["dienst"])] += 1
                    windows[name].append(shift_window(day, *shift_times[slot["dienst"]]))
                    worked_days[name].add(day)

        for slot in slots:
            for name in slot["filled"]:
                assignments.append({"datum": day, "dienst": slot["dienst"], "naam": name})

    return pd.DataFrame(assignments, columns=["datum", "dienst", "naam"])


def check_coverage(
    assignments: pd.DataFrame,
    team: pd.DataFrame,
    shifts: pd.DataFrame,
    rules: pd.DataFrame,
    calendar: pd.DataFrame,
) -> pd.DataFrame:
    """Bezetting per dag en dienst: nodig vs. ingepland."""
    level = dict(zip(team["naam"], team["seniority"]))
    shift_order = list(shifts["dienst"])
    rows = []
    for day in calendar.sort_values("datum").itertuples():
        planned = assignments[assignments["datum"] == day.datum]
        needed = {s["dienst"]: s for s in slots_for_day(day.dagtype, rules, shift_order)}
        for dienst in shift_order:
            names = list(planned.loc[planned["dienst"] == dienst, "naam"])
            slot = needed.get(dienst)
            nodig = slot["aantal"] if slot else 0
            nodig_senior = slot["min_senior"] if slot else 0
            if nodig == 0 and not names:
                continue
            seniors = sum(is_senior(level.get(n, "")) for n in names)
            if len(names) < nodig:
                status = STATUS_SHORT
            elif seniors < nodig_senior:
                status = STATUS_NO_SENIOR
            elif len(names) > nodig:
                status = STATUS_OVER
            else:
                status = STATUS_OK
            rows.append(
                {
                    "datum": day.datum,
                    "dagtype": day.dagtype,
                    "dienst": dienst,
                    "nodig": nodig,
                    "ingepland": len(names),
                    "senior nodig": nodig_senior,
                    "senior ingepland": seniors,
                    "status": status,
                    "mensen": ", ".join(names),
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "datum", "dagtype", "dienst", "nodig", "ingepland",
            "senior nodig", "senior ingepland", "status", "mensen",
        ],
    )


def check_conflicts(
    assignments: pd.DataFrame,
    team: pd.DataFrame,
    shifts: pd.DataFrame,
    absences: pd.DataFrame,
    min_rest_hours: float = 11,
) -> pd.DataFrame:
    """Regels die geschonden worden in het (handmatig aangepaste) rooster."""
    info = {r.naam: r for r in team.itertuples()}
    shift_times = {r.dienst: (r.start, r.eind) for r in shifts.itertuples()}
    absence_map = _absence_map(absences)
    min_rest = timedelta(hours=min_rest_hours)
    issues = []

    def add(row, message):
        issues.append({"datum": row["datum"], "naam": row["naam"], "dienst": row["dienst"], "probleem": message})

    for name, group in assignments.groupby("naam"):
        person = info.get(name)
        group = group.sort_values("datum")
        for _, row in group.iterrows():
            if person is None:
                add(row, "Not in the team")
                continue
            if not person.actief:
                add(row, "Not active")
            if row["datum"].weekday() in parse_free_days(person.vrije_dagen):
                add(row, "Fixed day off")
            if _is_absent(name, row["datum"], absence_map):
                add(row, "Absent")
        for day, count in group["datum"].value_counts().items():
            if count > 1:
                add({"datum": day, "naam": name, "dienst": "-"}, f"{count} shifts on one day")
        if person is not None:
            weeks = group["datum"].map(week_key).value_counts()
            for (year, week), count in weeks.items():
                if count > person.max_per_week:
                    issues.append({
                        "datum": None, "naam": name, "dienst": "-",
                        "probleem": f"Week {week}: {count} shifts (max {person.max_per_week})",
                    })
        timed = sorted(
            (
                (shift_window(r["datum"], *shift_times[r["dienst"]]), r)
                for _, r in group.iterrows()
            if r["dienst"] in shift_times
            ),
            key=lambda item: item[0],
        )
        for (prev_window, _), (window, row) in zip(timed, timed[1:]):
            gap = window[0] - prev_window[1]
            if timedelta(0) <= gap < min_rest:
                add(row, f"Only {gap.total_seconds() / 3600:.0f} hours rest after previous shift")

    return pd.DataFrame(issues, columns=["datum", "naam", "dienst", "probleem"])
