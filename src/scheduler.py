"""Roosterlogica: automatisch inroosteren en controleren van bezetting.

Twee diensten (dag en avond) en drie kanalen (main, app, NL). Per dag en dienst bepalen de
bezettingsregels hoeveel mensen er per kanaal nodig zijn; belangrijke wedstrijden en events
krijgen extra bezetting op main en app.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

import pandas as pd

SENIORITY_LEVELS = ["Intern", "Junior", "Medior", "Senior", "Lead"]
INTERN = "Intern"  # stagiairs werken altijd ma t/m vr (bij voorkeur overdag), nooit in het weekend
WEEKDAYS_ONLY = {0, 1, 2, 3, 4}
SHIFT_HOURS = 8  # dag- en avonddienst; contracturen / 8 = aantal diensten per week
SENIOR_LEVELS = {"Senior", "Lead"}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# Vaste (vrije) dagen mogen in het Engels of Nederlands ("mon, wed" of "ma, wo").
_DAY_PREFIXES = [("mo", "ma"), ("tu", "di"), ("we", "wo"), ("th", "do"), ("fr", "vr"), ("sa", "za"), ("su", "zo")]

CHANNELS = ["main", "app", "nl"]
CHANNEL_LABELS = {"main": "Main", "app": "App", "nl": "NL"}
DAY, EVENING, NIGHT = "Day", "Evening", "Night"
FRIDAY, SATURDAY, SUNDAY = 4, 5, 6
UCL_DAY_TYPE = "Champions League"
TOP_CLUBS = 10  # twee clubs uit de top 10 van clubs.py tegen elkaar = belangrijke wedstrijd
EXTRA_CHANNELS = ("main", "app")  # extra bezetting bij belangrijke wedstrijden en events
EVENING_FROM = time(15, 0)  # aftrap vanaf 15:00 valt in de avonddienst

STATUS_OK = "OK"
STATUS_SHORT = "Understaffed"
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


def channels_of(value) -> list[str]:
    """'main, app' -> ['main', 'app']"""
    chosen = {p.strip().lower() for p in str(value or "").split(",")}
    return [c for c in CHANNELS if c in chosen]


def week_key(day: date) -> tuple[int, int]:
    iso = day.isocalendar()
    return iso[0], iso[1]


def is_weekend(day: date) -> bool:
    return day.weekday() >= SATURDAY


def auto_day_type(day: date, ucl_days: set[date]) -> str:
    if day in ucl_days:
        return UCL_DAY_TYPE
    return "Weekend" if is_weekend(day) else "Regular day"


def shift_for_kickoff(kickoff: str) -> str:
    return EVENING if parse_time(kickoff) >= EVENING_FROM else DAY


def is_friday_nl(day: date, dienst: str, kanaal: str) -> bool:
    """Vrijdagavond NL (KKD + Eredivisie): een van de belangrijkste NL-shifts, op roulatie."""
    return kanaal == "nl" and dienst == EVENING and day.weekday() == FRIDAY


def priority(day: date, dienst: str, kanaal: str) -> int:
    """Lager = eerder inplannen. De belangrijkste NL-shifts en het weekend gaan voor."""
    if kanaal == "nl" and (
        (day.weekday() == SATURDAY and dienst == EVENING)
        or (day.weekday() == SUNDAY and dienst == DAY)
        or is_friday_nl(day, dienst, kanaal)
    ):
        return 0
    if is_weekend(day):
        return 1  # het weekend op volgorde (za dag, za avond, zo dag, zo avond): zie generate_schedule
    return 3 if dienst == EVENING else 4


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


def requirements(calendar: pd.DataFrame, rules: pd.DataFrame) -> pd.DataFrame:
    """Benodigde bezetting per datum, dienst en kanaal.

    `calendar` heeft per datum een dagtype en optioneel een extra (reden + dienst) voor
    belangrijke wedstrijden en events: daar komt op main en app iemand bij.
    """
    rows = []
    by_type = {dt: g for dt, g in rules.groupby("dagtype")}
    for day in calendar.itertuples():
        extra, extra_shift = getattr(day, "extra", ""), getattr(day, "extra_dienst", "")
        for r in by_type.get(day.dagtype, rules.iloc[0:0]).itertuples():
            for kanaal in CHANNELS:
                nodig, reden = int(getattr(r, kanaal)), ""
                if extra and r.dienst == extra_shift and kanaal in EXTRA_CHANNELS:
                    nodig, reden = nodig + 1, extra
                if nodig > 0:
                    rows.append({"datum": day.datum, "dagtype": day.dagtype, "dienst": r.dienst,
                                 "kanaal": kanaal, "nodig": nodig, "reden": reden})
    return pd.DataFrame(rows, columns=["datum", "dagtype", "dienst", "kanaal", "nodig", "reden"])


def generate_schedule(
    team: pd.DataFrame,
    shifts: pd.DataFrame,
    rules: pd.DataFrame,
    calendar: pd.DataFrame,
    absences: pd.DataFrame,
    min_rest_hours: float = 11,
    seed: int = 0,
    existing: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Maak een roostervoorstel (datum, dienst, kanaal, naam).

    Per week: eerst de vaste afspraken (UCL-avonden, vaste dagen, stagiairs ma t/m vr), dan de
    belangrijkste NL-shifts, het weekend, de avonden en de dagdiensten. Iedereen krijgt zo mogelijk
    minimaal één doordeweekse avond en één weekendshift; wie verst onder zijn contracturen zit gaat
    voor, en wie een bepaalde shift (bijv. zaterdagavond) vaak had, komt daar minder snel terug.
    Tot slot wordt iedereen aangevuld tot zijn contracturen (extra bezetting, binnen de regels).

    `existing`: diensten die al vastliggen. Binnen de periode blijven ze staan en vult de planner
    alleen de rest aan; erbuiten (bijv. de zondag ervoor) tellen ze mee voor rusttijd en max per week.
    """
    rng = random.Random(seed)
    min_rest = timedelta(hours=min_rest_hours)
    shift_times = {r.dienst: (r.start, r.eind) for r in shifts.itertuples()}
    absence_map = _absence_map(absences)
    day_types = dict(zip(calendar["datum"], calendar["dagtype"]))

    def target(r) -> int:
        """Aantal diensten per week volgens het contract (anders het maximum)."""
        contract = int(getattr(r, "contracturen", 0) or 0)
        return min(int(r.max_per_week), round(contract / SHIFT_HOURS)) if contract else int(r.max_per_week)

    people = {
        r.naam: {
            "channels": channels_of(r.kanalen),
            "max_week": int(r.max_per_week),
            "target": target(r),
            "intern": r.seniority == INTERN,
            "free": parse_free_days(r.vrije_dagen) | ({SATURDAY, SUNDAY} if r.seniority == INTERN else set()),
            "fixed_shift": str(r.vaste_dienst or ""),
            "fixed_days": parse_free_days(r.vaste_dagen) | (WEEKDAYS_ONLY if r.seniority == INTERN else set()),
            "ucl": bool(r.ucl),
        }
        for r in team[team["actief"] & team["auto"]].itertuples()
    }

    total = Counter()
    per_week = Counter()
    same_slot = Counter()  # (naam, weekdag, dienst): afwisselen, bijv. niet elke zaterdagavond dezelfde
    weekend_shifts = Counter()  # (naam, week)
    weekday_evenings = Counter()  # (naam, week)
    windows: dict[str, list] = defaultdict(list)
    worked_days: dict[str, set] = defaultdict(set)

    slots = [
        {"datum": r.datum, "dienst": r.dienst, "kanaal": r.kanaal, "copy": copy, "naam": None}
        for r in requirements(calendar, rules).itertuples()
        if r.dienst in shift_times
        for copy in range(r.nodig)
    ]

    def eligible(name: str, slot: dict) -> bool:
        info, day, dienst = people[name], slot["datum"], slot["dienst"]
        if slot["kanaal"] not in info["channels"]:
            return False
        if info["fixed_shift"] and info["fixed_shift"] != dienst:
            return False
        if day.weekday() in info["free"] or day in worked_days[name]:
            return False
        if per_week[(name, week_key(day))] >= info["max_week"]:
            return False
        if _is_absent(name, day, absence_map):
            return False
        start, end = shift_window(day, *shift_times[dienst])
        return _rest_ok(windows[name], start, end, min_rest)

    def assign(name: str, slot: dict) -> None:
        day, dienst, week = slot["datum"], slot["dienst"], week_key(slot["datum"])
        slot["naam"] = name
        total[name] += 1
        per_week[(name, week)] += 1
        same_slot[(name, day.weekday(), dienst)] += 1
        if is_weekend(day):
            weekend_shifts[(name, week)] += 1
        elif dienst == EVENING:
            weekday_evenings[(name, week)] += 1
        windows[name].append(shift_window(day, *shift_times[dienst]))
        worked_days[name].add(day)

    def score(name: str, slot: dict):
        info, day, week = people[name], slot["datum"], week_key(slot["datum"])
        cap = max(info["target"], 1)
        still_needs = (is_weekend(day) and weekend_shifts[(name, week)] == 0) or (
            not is_weekend(day) and slot["dienst"] == EVENING and weekday_evenings[(name, week)] == 0
        )
        return (
            0 if still_needs else 1,
            # Wie nog onder zijn contracturen zit, gaat voor.
            0 if per_week[(name, week)] < info["target"] else 1,
            # Spaar wie nog andere open diensten rond deze dag kan doen (bijv. zondagavond naar wie
            # zaterdagavond werkte, die mag zondagochtend toch niet door de rusttijd).
            flexibility(name, slot),
            # Wie alleen deze dienst kan (bijv. Francisco: alleen avond), gaat voor.
            0 if info["fixed_shift"] == slot["dienst"] else 1,
            per_week[(name, week)] / cap,
            round(total[name] / cap, 3),
            same_slot[(name, day.weekday(), slot["dienst"])],
            rng.random(),
        )

    def flexibility(name: str, slot: dict) -> int:
        near = {slot["datum"] - timedelta(days=1), slot["datum"], slot["datum"] + timedelta(days=1)}
        return sum(1 for s in slots if s["naam"] is None and s is not slot and s["datum"] in near and eligible(name, s))

    def open_slot(name: str, day: date, dienst: str | None, prefer: str | None = None) -> dict | None:
        options = [s for s in slots if s["naam"] is None and s["datum"] == day
                   and (dienst is None or s["dienst"] == dienst) and eligible(name, s)]
        options.sort(key=lambda s: (s["kanaal"] != prefer, s["copy"], priority(s["datum"], s["dienst"], s["kanaal"])))
        return options[0] if options else None

    in_period = set(calendar["datum"])
    for r in existing.itertuples() if existing is not None else []:
        if r.dienst not in shift_times:
            continue
        if r.datum not in in_period:
            windows[r.naam].append(shift_window(r.datum, *shift_times[r.dienst]))
            worked_days[r.naam].add(r.datum)
            per_week[(r.naam, week_key(r.datum))] += 1
            continue
        slot = next((s for s in slots if s["naam"] is None and s["datum"] == r.datum
                     and s["dienst"] == r.dienst and s["kanaal"] == r.kanaal), None)
        if slot is None:
            slot = {"datum": r.datum, "dienst": r.dienst, "kanaal": r.kanaal, "copy": 99, "naam": None}
            slots.append(slot)
        assign(r.naam, slot)

    for week in sorted({week_key(s["datum"]) for s in slots}):
        week_days = sorted({s["datum"] for s in slots if week_key(s["datum"]) == week})

        # 1. Vaste afspraken: UCL-avonden (Rogier op main) en vaste dagen (Francisco op vrijdag).
        for day in week_days:
            for name, info in people.items():
                if info["ucl"] and day_types.get(day) == UCL_DAY_TYPE:
                    fixed = open_slot(name, day, EVENING, prefer="main")
                elif day.weekday() in info["fixed_days"]:
                    shift = info["fixed_shift"] or (DAY if info["intern"] else None)
                    fixed = open_slot(name, day, shift) or (None if info["fixed_shift"] else open_slot(name, day, None))
                else:
                    continue
                if fixed:
                    assign(name, fixed)

        # 2. De rest, belangrijkste shifts eerst.
        todo = [s for s in slots if s["naam"] is None and week_key(s["datum"]) == week]
        shift_order = list(shift_times)

        def order(s):
            prio = priority(s["datum"], s["dienst"], s["kanaal"])
            if prio == 1:  # weekend: op tijdsvolgorde, zodat de zondagochtend niet leeg blijft door de rusttijd
                return (prio, s["datum"], shift_order.index(s["dienst"]), s["copy"])
            return (prio, date.min, 0, s["copy"], s["datum"])

        todo.sort(key=order)
        for slot in todo:
            candidates = [n for n in people if eligible(n, slot)]
            if candidates:
                assign(min(candidates, key=lambda n: score(n, slot)), slot)

        # 3. Aanvullen tot de contracturen: extra diensten waar het het rustigst bezet is.
        busy = Counter((s["datum"], s["dienst"]) for s in slots if s["naam"])
        day_shifts = {day: [d for d in rules[rules["dagtype"] == day_types.get(day)]["dienst"] if d in shift_times]
                      for day in week_days}
        while True:
            short = sorted((n for n in people if per_week[(n, week)] < people[n]["target"]),
                           key=lambda n: per_week[(n, week)] - people[n]["target"])
            added = False
            for name in short:
                info = people[name]
                options = [
                    {"datum": day, "dienst": dienst, "kanaal": kanaal, "copy": 99, "naam": None}
                    for day in week_days for dienst in day_shifts[day] for kanaal in info["channels"]
                ]
                options = [o for o in options if eligible(name, o)]
                if not options:
                    continue
                best = min(options, key=lambda o: (
                    busy[(o["datum"], o["dienst"])],
                    0 if info["intern"] and o["dienst"] == DAY else 1,
                    same_slot[(name, o["datum"].weekday(), o["dienst"])],
                    rng.random(),
                ))
                assign(name, best)
                slots.append(best)
                busy[(best["datum"], best["dienst"])] += 1
                added = True
            if not added:
                break

    return pd.DataFrame(
        [{k: s[k] for k in ("datum", "dienst", "kanaal", "naam")} for s in slots if s["naam"]],
        columns=["datum", "dienst", "kanaal", "naam"],
    )


def check_coverage(assignments: pd.DataFrame, rules: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
    """Bezetting per dag, dienst en kanaal: nodig vs. ingepland."""
    need = requirements(calendar, rules).set_index(["datum", "dienst", "kanaal"])
    planned = assignments.groupby(["datum", "dienst", "kanaal"])["naam"].apply(list).to_dict()
    day_types = dict(zip(calendar["datum"], calendar["dagtype"]))
    keys = set(need.index) | {k for k in planned if k[0] in day_types}
    rows = []
    for key in sorted(keys, key=lambda k: (k[0], k[1], CHANNELS.index(k[2]) if k[2] in CHANNELS else 9)):
        names = planned.get(key, [])
        nodig = int(need.at[key, "nodig"]) if key in need.index else 0
        # Geen bezetting nodig (bijv. Night, alleen handmatig): wie er staat is dan gewoon OK.
        status = STATUS_SHORT if len(names) < nodig else STATUS_OVER if 0 < nodig < len(names) else STATUS_OK
        rows.append({
            "datum": key[0], "dagtype": day_types.get(key[0], ""), "dienst": key[1],
            "kanaal": CHANNEL_LABELS.get(key[2], key[2]), "nodig": nodig, "ingepland": len(names),
            "status": status, "mensen": ", ".join(names),
            "reden": need.at[key, "reden"] if key in need.index else "",
        })
    return pd.DataFrame(
        rows, columns=["datum", "dagtype", "dienst", "kanaal", "nodig", "ingepland", "status", "mensen", "reden"]
    )


def assignment_problem(
    name: str,
    day: date,
    dienst: str,
    kanaal: str,
    others: pd.DataFrame,
    team: pd.DataFrame,
    shifts: pd.DataFrame,
    absences: pd.DataFrame,
    min_rest_hours: float = 11,
) -> str | None:
    """Waarom mag `name` deze dienst niet doen? None = mag wel. `others` = de rest van het rooster."""
    person = {r.naam: r for r in team.itertuples()}.get(name)
    if person is None:
        return f"{name} is not an employee"
    if not person.actief:
        return f"{name} is not active"
    if kanaal not in channels_of(person.kanalen):
        return f"{name} does not work on {CHANNEL_LABELS.get(kanaal, kanaal)}"
    if person.vaste_dienst and dienst != person.vaste_dienst:
        return f"{name} only works {person.vaste_dienst}"
    if day.weekday() in parse_free_days(person.vrije_dagen):
        return f"{name} has {WEEKDAYS[day.weekday()]} off"
    reasons = [r.reden for r in absences.itertuples() if r.naam == name and r.van <= day <= r.tot]
    if reasons:
        return f"{name} is not available ({reasons[0]})" if reasons[0] else f"{name} is not available"
    mine = others[others["naam"] == name]
    if (mine["datum"] == day).any():
        return f"{name} already works on {WEEKDAYS[day.weekday()]} {day:%d-%m}"
    shift_times = {r.dienst: (r.start, r.eind) for r in shifts.itertuples()}
    if dienst in shift_times:
        start, end = shift_window(day, *shift_times[dienst])
        windows = [shift_window(r.datum, *shift_times[r.dienst]) for r in mine.itertuples() if r.dienst in shift_times]
        if not _rest_ok(windows, start, end, timedelta(hours=min_rest_hours)):
            return f"{name} needs {min_rest_hours:g} hours rest between shifts"
    if (mine["datum"].map(week_key) == week_key(day)).sum() >= person.max_per_week:
        return f"{name} already has {person.max_per_week} shifts that week (max)"
    return None


def drop_conflicts(
    assignments: pd.DataFrame,
    team: pd.DataFrame,
    shifts: pd.DataFrame,
    absences: pd.DataFrame,
    min_rest_hours: float = 11,
    context: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Haal diensten weg die tegen een regel ingaan, tot er niets meer botst.

    `context`: diensten buiten de periode (bijv. de dag ervoor); die tellen mee voor de rusttijd
    maar worden zelf niet weggehaald.
    """
    result = assignments.reset_index(drop=True)
    context = context if context is not None else result.iloc[0:0]
    for _ in range(50):
        combined = pd.concat([context, result], ignore_index=True)
        issues = check_conflicts(combined, team, shifts, absences, min_rest_hours)
        issues = issues[issues["datum"].notna() & issues["datum"].isin(set(result["datum"]))]
        if issues.empty:
            break
        drop = set()
        for issue in issues.itertuples():
            rows = result[(result["datum"] == issue.datum) & (result["naam"] == issue.naam)]
            if issue.dienst == "-":  # meerdere diensten op een dag: de eerste blijft staan
                drop |= set(rows.index[1:])
            else:
                drop |= set(rows[rows["dienst"] == issue.dienst].index[:1])
        if not drop:
            break
        result = result.drop(index=sorted(drop)).reset_index(drop=True)
    return result


def check_conflicts(
    assignments: pd.DataFrame,
    team: pd.DataFrame,
    shifts: pd.DataFrame,
    absences: pd.DataFrame,
    min_rest_hours: float = 11,
    period: list[date] | None = None,
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
                add(row, "Not an employee")
                continue
            if not person.actief:
                add(row, "Not active")
            if row["kanaal"] not in channels_of(person.kanalen):
                add(row, f"Does not work on {CHANNEL_LABELS.get(row['kanaal'], row['kanaal'])}")
            if person.vaste_dienst and row["dienst"] != person.vaste_dienst:
                add(row, f"Always works {person.vaste_dienst}")
            if row["datum"].weekday() in parse_free_days(person.vrije_dagen):
                add(row, "Fixed day off")
            if _is_absent(name, row["datum"], absence_map):
                add(row, "Absent")
        for day, count in group["datum"].value_counts().items():
            if count > 1:
                add({"datum": day, "naam": name, "dienst": "-"}, f"{count} shifts on one day")
        if person is not None:
            for (_, week), count in group["datum"].map(week_key).value_counts().items():
                if count > person.max_per_week:
                    issues.append({"datum": None, "naam": name, "dienst": "-",
                                   "probleem": f"Week {week}: {count} shifts (max {person.max_per_week})"})
        timed = sorted(
            ((shift_window(r["datum"], *shift_times[r["dienst"]]), r) for _, r in group.iterrows()
             if r["dienst"] in shift_times),
            key=lambda item: item[0],
        )
        for (prev_window, _), (window, row) in zip(timed, timed[1:]):
            gap = window[0] - prev_window[1]
            if timedelta(0) <= gap < min_rest:
                add(row, f"Only {gap.total_seconds() / 3600:.0f} hours rest after previous shift")

    # Iedereen minimaal één doordeweekse avond en één weekendshift per volledige week (tenzij afwezig).
    for week in sorted({week_key(d) for d in period or []}):
        days = [d for d in period if week_key(d) == week]
        if len(days) < 7:
            continue
        in_week = assignments[assignments["datum"].map(week_key) == week]
        for person in team[team["actief"] & team["auto"]].itertuples():
            if any(_is_absent(person.naam, d, absence_map) for d in days):
                continue
            mine = in_week[in_week["naam"] == person.naam]
            weekend = mine["datum"].map(is_weekend).astype(bool)
            if not weekend.any() and person.seniority != INTERN:
                issues.append({"datum": None, "naam": person.naam, "dienst": "-",
                               "probleem": f"Week {week[1]}: no weekend shift"})
            if not ((~weekend) & (mine["dienst"] == EVENING)).any() and person.seniority != INTERN:
                issues.append({"datum": None, "naam": person.naam, "dienst": "-",
                               "probleem": f"Week {week[1]}: no weekday evening"})

    return pd.DataFrame(issues, columns=["datum", "naam", "dienst", "probleem"])
