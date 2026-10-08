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
# Weekend: zo nodig 2 diensten (za én zo), maar na een weekend met 2 is het weekend erna (en ervoor) maximaal 1.
MAX_WEEKEND_SHIFTS = 2


def _prev_week(week: tuple[int, int], delta: int) -> tuple[int, int]:
    monday = date.fromisocalendar(week[0], week[1], 1) + timedelta(weeks=delta)
    return week_key(monday)
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
# Extra bezetting bij wedstrijden en events (tab Staffing rules): soorten aanleidingen.
TRIGGER_EVENT = "Special event"  # elk special event (avonddienst)
TRIGGER_BIG = "Big match"  # twee clubs uit de top van clubs.py tegen elkaar
TRIGGER_TEAM = "Team plays"  # waarde: team, bijv. Netherlands of Ajax
TRIGGER_COMPETITION = "Competition"  # waarde: (deel van) de competitienaam, bijv. Nations League
TRIGGER_DUTCH = "Dutch club in competition"  # waarde: competitie; een club uit clubs.DUTCH_CLUBS speelt
TRIGGERS = [TRIGGER_TEAM, TRIGGER_DUTCH, TRIGGER_COMPETITION, TRIGGER_BIG, TRIGGER_EVENT]
EVENING_FROM = time(15, 0)  # aftrap vanaf 15:00 valt in de avonddienst

# Regels per persoon (tab Staffing rules → Person rules): (naam, regel, waarde).
RULE_NOT_ALONE = "Never alone on channel"  # waarde: kanaal; er moet nog iemand op dat kanaal in die dienst
RULE_NO_SHIFT = "Never on shift"  # waarde: dienst, of dienst op bepaalde dagen: "Day: sat", "Evening: fri"
RULE_NOT_WITH = "Never together with"  # waarde: andere persoon; nooit samen in dezelfde dienst
RULE_ONLY_CHANNEL = "Only on channel"  # waarde: kanaal of kanalen, bijv. "main" of "main, app"
RULE_ONLY_SHIFT = "Only on shift"  # waarde: dienst(en), bijv. "Day"
RULE_ONLY_DAYS = "Only on days"  # waarde: dagen, bijv. "mon, tue, wed, thu, fri" (alleen doordeweeks)
RULE_NEVER_DAYS = "Never on days"  # waarde: dagen, bijv. "sun"
RULE_PREFERS = "Prefers shift"  # voorkeur (geen harde regel): "Evening", of voor bepaalde dagen "Day: mon, fri"
RULE_ALWAYS = "Always works"  # harde regel: "Day: mon, fri" = op ma en vr altijd de dagdienst (bijv. kantoor)
# Nuluren (tab Zero hours): intern als regel met waarde "2026-10-12:Day;2026-10-14:Any". Alleen op die dagen
# (en dan ook echt), nooit aanvullen tot contracturen. Niet zelf in Person rules te kiezen.
RULE_ZERO = "Zero hours"
# Uit Employees (niet zelf in Person rules te kiezen): avonden pas vanaf een datum, en avonden alleen samen.
RULE_EVENING_FROM = "Evenings from"  # waarde: datum (YYYY-MM-DD)
RULE_EVENING_TOGETHER = "Evenings only together"  # 's avonds nog iemand op hetzelfde kanaal
ANY_SHIFT = "Any"
RULE_TYPES = [RULE_ALWAYS, RULE_PREFERS, RULE_ONLY_CHANNEL, RULE_ONLY_SHIFT, RULE_ONLY_DAYS, RULE_NEVER_DAYS,
              RULE_NOT_ALONE, RULE_NO_SHIFT, RULE_NOT_WITH]
# Zo vul je de waarde in, per soort regel (uitleg in de app).
RULE_VALUES = {
    RULE_ALWAYS: "shift: days, e.g. Day: mon, fri (always scheduled in that shift on those days)",
    RULE_PREFERS: "a shift, or a shift for some days: Evening · Day: mon, fri (a preference, not a hard rule)",
    RULE_ONLY_CHANNEL: "channel(s): main, app, nl",
    RULE_ONLY_SHIFT: "shift(s): Day, Evening, Night",
    RULE_ONLY_DAYS: "days, e.g. mon, tue, wed, thu, fri",
    RULE_NEVER_DAYS: "days, e.g. sat, sun",
    RULE_NOT_ALONE: "a channel: main, app, nl",
    RULE_NO_SHIFT: "a shift, or a shift on some days: Night · Day: sat · Evening: fri",
    RULE_NOT_WITH: "another person",
}


def preferred_shift(name: str, day: date, person_rules: pd.DataFrame | None) -> str | None:
    """Voorkeursdienst van `name` op deze dag ('Day: mon, fri' gaat voor een algemene 'Evening')."""
    general = specific = None
    for r in (person_rules.itertuples() if person_rules is not None else []):
        if r.naam != name or r.regel != RULE_PREFERS:
            continue
        shift, _, days = str(r.waarde).partition(":")
        shift = shift.strip().capitalize()
        if not days.strip():
            general = shift
        elif day.weekday() in parse_free_days(days):
            specific = shift
    return specific or general


def _rows(person_rules) -> list:
    """Persoonsregels als lijst rijen. Een lijst wordt niet opnieuw omgezet: zo hoeft check_conflicts de tabel maar
    één keer door te lopen i.p.v. bij elke dienst opnieuw (itertuples is traag)."""
    if person_rules is None:
        return []
    return person_rules if isinstance(person_rules, list) else list(person_rules.itertuples())


def zero_hour_days(name: str, person_rules) -> dict[date, str] | None:
    """Nuluren: {datum: dienst of 'Any'} waarop `name` werkt; None als die persoon geen nuluren heeft."""
    result = None
    for r in _rows(person_rules):
        if r.naam == name and r.regel == RULE_ZERO:
            result = result or {}
            for part in str(r.waarde or "").split(";"):
                if ":" in part:
                    day, shift = part.split(":", 1)
                    result[date.fromisoformat(day.strip())] = shift.strip() or ANY_SHIFT
    return result


def always_works(name: str, person_rules: pd.DataFrame | None) -> list[tuple[str, set[int]]]:
    """[(dienst, {weekdagen})] uit 'Always works'-regels, bijv. [("Day", {0, 4})]."""
    result = []
    for r in (person_rules.itertuples() if person_rules is not None else []):
        if r.naam == name and r.regel == RULE_ALWAYS:
            shift, _, days = str(r.waarde).partition(":")
            result.append((shift.strip().capitalize(), parse_free_days(days)))
    return result


def _shift_on_days(value) -> tuple[str, set[int] | None]:
    """'Evening: fri' -> ('evening', {4}); 'Night' -> ('night', None) = alle dagen."""
    shift, _, days = str(value).partition(":")
    return shift.strip().lower(), (parse_free_days(days) if days.strip() else None)


def _items(value) -> set[str]:
    return {p.strip().lower() for p in str(value or "").replace(";", ",").split(",") if p.strip()}

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


def is_weekend_shift(day: date, dienst: str) -> bool:
    """Telt mee als weekenddienst: zaterdag, zondag en vrijdagavond."""
    return is_weekend(day) or (day.weekday() == FRIDAY and dienst == EVENING)


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
        return 1 if dienst == EVENING else 2  # weekend: avonden (met topduels) gaan voor op dagdiensten
    return 3  # doordeweeks: dag en avond samen, zodat schaarse mensen (bijv. app) overal terechtkomen


def _absence_map(absences: pd.DataFrame) -> dict[str, list[tuple[date, date, str]]]:
    """{naam: [(van, tot, deel)]}; deel = "" (hele dag), "Day" of "Evening" (alleen dat dagdeel vrij)."""
    result: dict[str, list[tuple[date, date, str]]] = defaultdict(list)
    for row in absences.itertuples():
        start, end = row.van, row.tot if pd.notna(row.tot) else row.van
        part = str(getattr(row, "deel", "") or "")
        result[row.naam].append((min(start, end), max(start, end), part))
    return result


def _is_absent(name: str, day: date, absence_map, dienst: str | None = None) -> bool:
    """Afwezig op die dag (en in die dienst)? Zonder dienst telt alleen een hele vrije dag."""
    return any(start <= day <= end and (not part or (dienst is not None and part == dienst))
               for start, end, part in absence_map.get(name, []))


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

    `calendar` heeft per datum een dagtype en optioneel `extras`: een lijst (dienst, kanaal, aantal,
    reden) met extra bezetting voor wedstrijden en events (zie de tabel in Staffing rules).
    """
    rows = []
    by_type = {dt: g for dt, g in rules.groupby("dagtype")}
    for day in calendar.itertuples():
        bonus, why = Counter(), defaultdict(list)
        for dienst, kanaal, n, reden in getattr(day, "extras", None) or []:
            bonus[(dienst, kanaal)] += n
            if reden not in why[(dienst, kanaal)]:
                why[(dienst, kanaal)].append(reden)
        base = {(r.dienst, k): int(getattr(r, k)) for r in by_type.get(day.dagtype, rules.iloc[0:0]).itertuples()
                for k in CHANNELS}
        for (dienst, kanaal) in list(base) + [key for key in bonus if key not in base]:
            nodig = base.get((dienst, kanaal), 0) + bonus[(dienst, kanaal)]
            if nodig > 0:
                rows.append({"datum": day.datum, "dagtype": day.dagtype, "dienst": dienst, "kanaal": kanaal,
                             "nodig": nodig, "reden": " / ".join(why[(dienst, kanaal)])})
    return pd.DataFrame(rows, columns=["datum", "dagtype", "dienst", "kanaal", "nodig", "reden"])


def rule_breaks(name: str, day: date, dienst: str, kanaal: str, crew: list[tuple[str, str]],
                person_rules: pd.DataFrame | None, planning: bool = False) -> list[str]:
    """Welke persoonsregels breekt `name` op deze dienst? `crew` = de anderen in die dienst (naam, kanaal).

    `planning`: ook "Never alone on channel" (de planner zet zo iemand alleen naast een ander). Met de hand
    mag het wel: dan telt die persoon niet als bezetting en blijft de plek open (zie check_coverage)."""
    rows = _rows(person_rules)
    if not rows:
        return []
    others = [(n, k) for n, k in crew if n != name]
    problems = []
    for r in rows:
        if r.regel == RULE_NOT_WITH and name in (r.naam, r.waarde):
            partner = r.waarde if name == r.naam else r.naam
            if any(n == partner for n, _ in others):
                problems.append(f"{name} may not work together with {partner}")
        if r.naam != name:
            continue
        if r.regel == RULE_NO_SHIFT:
            shift, days = _shift_on_days(r.waarde)
            if dienst.lower() == shift and (days is None or day.weekday() in days):
                problems.append(f"{name} never works {dienst}" + ("" if days is None else f" on {WEEKDAYS[day.weekday()]}"))
        if r.regel == RULE_ONLY_CHANNEL and kanaal not in _items(r.waarde):
            problems.append(f"{name} only works on {r.waarde}")
        if r.regel == RULE_ONLY_SHIFT and dienst.lower() not in _items(r.waarde):
            problems.append(f"{name} only works {r.waarde}")
        if r.regel == RULE_ONLY_DAYS and day.weekday() not in parse_free_days(r.waarde):
            problems.append(f"{name} only works on {r.waarde}")
        if r.regel == RULE_EVENING_FROM and dienst == EVENING and day < date.fromisoformat(str(r.waarde)):
            problems.append(f"{name} works evenings only from {date.fromisoformat(str(r.waarde)):%d-%m-%Y}")
        if r.regel == RULE_EVENING_TOGETHER and dienst == EVENING and not any(k == kanaal for _, k in others):
            problems.append(f"{name} may only work an evening together with someone on "
                            f"{CHANNEL_LABELS.get(kanaal, kanaal)}")
        if r.regel == RULE_ZERO:
            agreed = zero_hour_days(name, person_rules) or {}
            if day not in agreed:
                problems.append(f"{name} (zero hours) only works on the agreed days")
            elif agreed[day] != ANY_SHIFT and agreed[day] != dienst:
                problems.append(f"{name} (zero hours) works {agreed[day]} on {day:%d-%m}")
        if r.regel == RULE_ALWAYS:
            shift, _, days = str(r.waarde).partition(":")
            if day.weekday() in parse_free_days(days) and dienst.lower() != shift.strip().lower():
                problems.append(f"{name} always works {shift.strip()} on {WEEKDAYS[day.weekday()]}")
        if r.regel == RULE_NEVER_DAYS and day.weekday() in parse_free_days(r.waarde):
            problems.append(f"{name} never works on {WEEKDAYS[day.weekday()]}")
        if planning and r.regel == RULE_NOT_ALONE and kanaal == r.waarde and not any(k == kanaal for _, k in others):
            problems.append(f"{name} may not be alone on {CHANNEL_LABELS.get(kanaal, kanaal)}")
    return problems


def generate_schedule(
    team: pd.DataFrame,
    shifts: pd.DataFrame,
    rules: pd.DataFrame,
    calendar: pd.DataFrame,
    absences: pd.DataFrame,
    min_rest_hours: float = 11,
    seed: int = 0,
    existing: pd.DataFrame | None = None,
    person_rules: pd.DataFrame | None = None,
    use_optimizer: bool = True,
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
    # Eerst de optimalisatie (alle weken tegelijk); lukt die niet, dan de planner hieronder (plek voor plek).
    if use_optimizer:
        import optimizer
        result = optimizer.optimize_schedule(team, shifts, rules, calendar, absences, min_rest_hours, seed,
                                             existing, person_rules)
        if result is not None:
            return result

    rng = random.Random(seed)
    min_rest = timedelta(hours=min_rest_hours)
    shift_times = {r.dienst: (r.start, r.eind) for r in shifts.itertuples()}
    absence_map = _absence_map(absences)
    day_types = dict(zip(calendar["datum"], calendar["dagtype"]))

    def target(r) -> int:
        """Aantal diensten per week volgens het contract (anders het maximum); nuluren: niet aanvullen."""
        if zero_hour_days(r.naam, person_rules) is not None:
            return 0
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
            "no_evening": bool(getattr(r, "geen_avond", False)),
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
    crew: dict[tuple, list] = defaultdict(list)  # (datum, dienst) -> [(naam, kanaal)]

    slots = [
        {"datum": r.datum, "dienst": r.dienst, "kanaal": r.kanaal, "copy": copy, "naam": None}
        for r in requirements(calendar, rules).itertuples()
        if r.dienst in shift_times
        for copy in range(r.nodig)
    ]

    def eligible(name: str, slot: dict, planning: bool = True) -> bool:
        info, day, dienst = people[name], slot["datum"], slot["dienst"]
        if slot["kanaal"] not in info["channels"]:
            return False
        if info["fixed_shift"] and info["fixed_shift"] != dienst:
            return False
        if info["no_evening"] and dienst == EVENING:
            return False
        if day.weekday() in info["free"] or day in worked_days[name]:
            return False
        if is_weekend_shift(day, dienst):  # za, zo en vrijdagavond
            week = week_key(day)
            done = weekend_shifts[(name, week)]
            if done >= MAX_WEEKEND_SHIFTS:
                return False
            # Tweede weekenddienst alleen als het weekend ervoor en erna er niet al 2 had.
            if done == 1 and (weekend_shifts[(name, _prev_week(week, -1))] >= 2
                              or weekend_shifts[(name, _prev_week(week, 1))] >= 2):
                return False
        if per_week[(name, week_key(day))] >= info["max_week"]:
            return False
        if _is_absent(name, day, absence_map, dienst):
            return False
        if rule_breaks(name, day, dienst, slot["kanaal"], crew[(day, dienst)], person_rules, planning=planning):
            return False
        start, end = shift_window(day, *shift_times[dienst])
        return _rest_ok(windows[name], start, end, min_rest)

    def assign(name: str, slot: dict) -> None:
        day, dienst, week = slot["datum"], slot["dienst"], week_key(slot["datum"])
        slot["naam"] = name
        crew[(day, dienst)].append((name, slot["kanaal"]))
        total[name] += 1
        per_week[(name, week)] += 1
        same_slot[(name, day.weekday(), dienst)] += 1
        if is_weekend_shift(day, dienst):
            weekend_shifts[(name, week)] += 1
        if not is_weekend(day) and dienst == EVENING:
            weekday_evenings[(name, week)] += 1
        windows[name].append(shift_window(day, *shift_times[dienst]))
        worked_days[name].add(day)

    def score(name: str, slot: dict):
        info, day, week = people[name], slot["datum"], week_key(slot["datum"])
        cap = max(info["target"], 1)
        still_needs = (is_weekend(day) and weekend_shifts[(name, week)] == 0) or (
            not is_weekend(day) and slot["dienst"] == EVENING and weekday_evenings[(name, week)] == 0
        )
        preferred = preferred_shift(name, day, person_rules)
        return (
            indispensable(name, slot),  # eerst: geen plek leeg laten omdat de enige kandidaat al ergens anders staat
            0 if still_needs else 1,
            # In het weekend eerst iedereen één keer; een tweede weekenddienst alleen als het niet anders kan.
            weekend_shifts[(name, week)] if is_weekend_shift(day, slot["dienst"]) else 0,
            # Voorkeur (bijv. Jonathan vooral avonden): wie deze dienst liever niet heeft, komt later aan bod.
            1 if preferred and preferred != slot["dienst"] else 0,
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

    def indispensable(name: str, slot: dict) -> int:
        """Voor hoeveel andere open plekken op die dag is `name` (bijna) de enige kandidaat? Bijv. Joshua
        als enige voor NL in het weekend: dan niet op main zetten."""
        count = 0
        for s in slots:
            if s["naam"] is None and s is not slot and s["datum"] == slot["datum"] and eligible(name, s):
                others = sum(1 for n in people if n != name and eligible(n, s))
                count += others == 0
        return count

    def flexibility(name: str, slot: dict) -> int:
        near = {slot["datum"] - timedelta(days=1), slot["datum"], slot["datum"] + timedelta(days=1)}
        return sum(1 for s in slots if s["naam"] is None and s is not slot and s["datum"] in near and eligible(name, s))

    def open_slot(name: str, day: date, dienst: str | None, prefer: str | None = None) -> dict | None:
        options = [s for s in slots if s["naam"] is None and s["datum"] == day
                   and (dienst is None or s["dienst"] == dienst) and eligible(name, s)]
        options.sort(key=lambda s: (s["kanaal"] != prefer, s["copy"], priority(s["datum"], s["dienst"], s["kanaal"])))
        return options[0] if options else None

    in_period = set(calendar["datum"])
    outside_period = Counter()
    for r in existing.itertuples() if existing is not None else []:
        if r.dienst not in shift_times:
            continue
        if r.datum not in in_period:
            windows[r.naam].append(shift_window(r.datum, *shift_times[r.dienst]))
            worked_days[r.naam].add(r.datum)
            per_week[(r.naam, week_key(r.datum))] += 1
            outside_period[(r.naam, week_key(r.datum))] += 1
            if is_weekend_shift(r.datum, r.dienst):
                weekend_shifts[(r.naam, week_key(r.datum))] += 1
            continue
        slot = next((s for s in slots if s["naam"] is None and s["datum"] == r.datum
                     and s["dienst"] == r.dienst and s["kanaal"] == r.kanaal), None)
        if slot is None:
            slot = {"datum": r.datum, "dienst": r.dienst, "kanaal": r.kanaal, "copy": 99, "naam": None}
            slots.append(slot)
        assign(r.naam, slot)

    # 1. Vaste afspraken eerst, voor de hele periode: Always works (bijv. Jonathan ma/vr overdag), UCL-avonden
    #    (Rogier op main) en vaste dagen (Francisco op vrijdag). Dan houdt de rest rekening met hun rusttijd.
    for day in sorted({s['datum'] for s in slots}):
        for name, info in people.items():
            agreed = zero_hour_days(name, person_rules)
            always = [s for s, days in always_works(name, person_rules) if day.weekday() in days]
            if agreed is not None and day in agreed:
                shift = None if agreed[day] == ANY_SHIFT else agreed[day]
                fixed = open_slot(name, day, shift)
                if fixed is None and shift:  # alle plekken al vol: dan een extra plek in die dienst
                    fixed = next(({"datum": day, "dienst": shift, "kanaal": k, "copy": 99, "naam": None}
                                  for k in info["channels"]
                                  if eligible(name, {"datum": day, "dienst": shift, "kanaal": k})), None)
                    if fixed:
                        slots.append(fixed)
            elif always:
                fixed = open_slot(name, day, always[0])
                if fixed is None:  # geen gewone plek (vol, of "never alone"): dan als extra in die dienst
                    fixed = next(({"datum": day, "dienst": always[0], "kanaal": k, "copy": 99, "naam": None}
                                  for k in info["channels"]
                                  if eligible(name, {"datum": day, "dienst": always[0], "kanaal": k}, planning=False)), None)
                    if fixed:
                        slots.append(fixed)
            elif info["ucl"] and day_types.get(day) == UCL_DAY_TYPE:
                fixed = open_slot(name, day, EVENING, prefer="main")
            elif day.weekday() in info["fixed_days"]:
                shift = info["fixed_shift"] or (DAY if info["intern"] else None)
                fixed = open_slot(name, day, shift) or (None if info["fixed_shift"] else open_slot(name, day, None))
            else:
                continue
            if fixed:
                assign(name, fixed)

    for week in sorted({week_key(s["datum"]) for s in slots}):
        week_days = sorted({s["datum"] for s in slots if week_key(s["datum"]) == week})

        # 2. De rest, belangrijkste shifts eerst.
        todo = [s for s in slots if s["naam"] is None and week_key(s["datum"]) == week]
        shift_order = list(shift_times)

        def order(s):
            prio = priority(s["datum"], s["dienst"], s["kanaal"])
            # Extra plekken (bijv. voor een topduel) eerst; doordeweeks de avond vóór de dag.
            return (prio, 0 if s["dienst"] == EVENING else 1, -s["copy"], s["datum"])

        todo.sort(key=order)
        # Krappe diensten eerst (2 of minder mensen beschikbaar, bijv. app: weinig mensen mogen dat), zodat
        # die mensen niet al op een andere dienst staan; de rest in de volgorde hierboven.
        while todo:
            options = [(s, [n for n in people if eligible(n, s)]) for s in todo]
            # Per groep (NL-sleuteldiensten, weekendavond, weekenddag, doordeweeks) de krapste plek eerst.
            slot, candidates = min(options, key=lambda o: (
                priority(o[0]["datum"], o[0]["dienst"], o[0]["kanaal"]),
                (0, len(o[1])) if 0 < len(o[1]) <= 2 else (1, 0),
                todo.index(o[0]),
            ))
            todo.remove(slot)
            if candidates:
                assign(min(candidates, key=lambda n: score(n, slot)), slot)

        # 3. Aanvullen tot de contracturen: extra diensten doordeweeks, waar de meeste wedstrijden per persoon zijn.
        busy = Counter((s["datum"], s["dienst"]) for s in slots if s["naam"])
        games = {(d.datum, sh): n for d in calendar.itertuples() for sh, n in (getattr(d, "wedstrijden", None) or {}).items()}
        # Alleen doordeweeks aanvullen: in het weekend staan alleen de mensen die de regels vragen.
        day_shifts = {day: [d for d in rules[rules["dagtype"] == day_types.get(day)]["dienst"] if d in shift_times]
                      if not is_weekend(day) else [] for day in week_days}
        # Een deels geplande week (bijv. deze week vanaf vandaag): het urendoel naar verhouding van de
        # doordeweekse dagen die nu gepland worden, bovenop wat er al stond. Anders komt alles op die paar dagen.
        weekdays_planned = sum(1 for d in week_days if not is_weekend(d))
        def week_target(n: str) -> int:
            already = outside_period[(n, week)]  # diensten in deze week buiten de planperiode (bijv. eerder deze week)
            return min(people[n]["target"], already + round(people[n]["target"] * weekdays_planned / 5))

        while True:
            short = sorted((n for n in people if per_week[(n, week)] < week_target(n)),
                           key=lambda n: per_week[(n, week)] - week_target(n))
            added = False
            for name in short:
                info = people[name]
                # Eerst open (nog lege) verplichte plekken in deze week, ook in het weekend; dan pas extra plekken.
                open_slots = [sl for sl in slots if sl["naam"] is None and week_key(sl["datum"]) == week
                              and eligible(name, sl)]
                options = open_slots or [
                    o for o in ({"datum": day, "dienst": dienst, "kanaal": kanaal, "copy": 99, "naam": None}
                                for day in week_days for dienst in day_shifts[day] for kanaal in info["channels"])
                    if eligible(name, o)
                ]
                if not options:
                    continue
                best = min(options, key=lambda o: (
                    1 if preferred_shift(name, o["datum"], person_rules) not in (None, o["dienst"]) else 0,
                    busy[(o["datum"], o["dienst"])] / (1 + games.get((o["datum"], o["dienst"]), 0)),
                    0 if info["intern"] and o["dienst"] == DAY else 1,
                    same_slot[(name, o["datum"].weekday(), o["dienst"])],
                    rng.random(),
                ))
                assign(name, best)
                if best not in open_slots:
                    slots.append(best)
                busy[(best["datum"], best["dienst"])] += 1
                added = True
            if not added:
                break

    return pd.DataFrame(
        [{k: s[k] for k in ("datum", "dienst", "kanaal", "naam")} for s in slots if s["naam"]],
        columns=["datum", "dienst", "kanaal", "naam"],
    )


def check_coverage(assignments: pd.DataFrame, rules: pd.DataFrame, calendar: pd.DataFrame,
                   person_rules: pd.DataFrame | None = None) -> pd.DataFrame:
    """Bezetting per dag, dienst en kanaal: nodig vs. ingepland.

    Wie "Never alone on channel" heeft en daar alleen staat (bijv. Maybel op NL), telt niet als bezetting."""
    not_alone = {(r.naam, r.waarde) for r in (person_rules.itertuples() if person_rules is not None else [])
                 if r.regel == RULE_NOT_ALONE}
    need = requirements(calendar, rules).set_index(["datum", "dienst", "kanaal"])
    planned = assignments.groupby(["datum", "dienst", "kanaal"])["naam"].apply(list).to_dict()
    day_types = dict(zip(calendar["datum"], calendar["dagtype"]))
    keys = set(need.index) | {k for k in planned if k[0] in day_types}
    rows = []
    for key in sorted(keys, key=lambda k: (k[0], k[1], CHANNELS.index(k[2]) if k[2] in CHANNELS else 9)):
        names = planned.get(key, [])
        nodig = int(need.at[key, "nodig"]) if key in need.index else 0
        counted = names if any((n, key[2]) not in not_alone for n in names) else []
        # Geen bezetting nodig (bijv. Night, alleen handmatig): wie er staat is dan gewoon OK.
        status = STATUS_SHORT if len(counted) < nodig else STATUS_OVER if 0 < nodig < len(counted) else STATUS_OK
        rows.append({
            "datum": key[0], "dagtype": day_types.get(key[0], ""), "dienst": key[1],
            "kanaal": CHANNEL_LABELS.get(key[2], key[2]), "nodig": nodig, "ingepland": len(counted),
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
    if getattr(person, "geen_avond", False) and dienst == EVENING:
        return f"{name} does not work evenings"
    if day.weekday() in parse_free_days(person.vrije_dagen):
        return f"{name} has {WEEKDAYS[day.weekday()]} off"
    reasons = [r.reden or (f"{r.deel} off" if getattr(r, "deel", "") else "")
               for r in absences.itertuples() if r.naam == name and r.van <= day <= r.tot
               and (not getattr(r, "deel", "") or r.deel == dienst)]
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
    person_rules: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Haal diensten weg die tegen een regel ingaan, tot er niets meer botst.

    `context`: diensten buiten de periode (bijv. de dag ervoor); die tellen mee voor de rusttijd
    maar worden zelf niet weggehaald.
    """
    result = assignments.reset_index(drop=True)
    context = context if context is not None else result.iloc[0:0]
    for _ in range(50):
        combined = pd.concat([context, result], ignore_index=True)
        issues = check_conflicts(combined, team, shifts, absences, min_rest_hours, person_rules=person_rules)
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
    person_rules: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Regels die geschonden worden in het (handmatig aangepaste) rooster."""
    info = {r.naam: r for r in team.itertuples()}
    shift_times = {r.dienst: (r.start, r.eind) for r in shifts.itertuples()}
    absence_map = _absence_map(absences)
    min_rest = timedelta(hours=min_rest_hours)
    issues = []

    def add(row, message):
        issues.append({"datum": row["datum"], "naam": row["naam"], "dienst": row["dienst"], "probleem": message})

    person_rules = _rows(person_rules)  # één keer omzetten, daarna overal de lijst gebruiken
    if person_rules:
        for (day, dienst), group in assignments.groupby(["datum", "dienst"]):
            crew = list(zip(group["naam"], group["kanaal"]))
            for _, row in group.iterrows():
                for message in rule_breaks(row["naam"], day, dienst, row["kanaal"], crew, person_rules):
                    add(row, message)

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
            if getattr(person, "geen_avond", False) and row["dienst"] == EVENING:
                add(row, "Does not work evenings")
            if row["datum"].weekday() in parse_free_days(person.vrije_dagen):
                add(row, "Fixed day off")
            if _is_absent(name, row["datum"], absence_map, row["dienst"]):
                add(row, "Absent")
        for day, count in group["datum"].value_counts().items():
            if count > 1:
                add({"datum": day, "naam": name, "dienst": "-"}, f"{count} shifts on one day")
        weekend = group[[is_weekend_shift(d, s) for d, s in zip(group["datum"], group["dienst"])]].sort_values("datum")
        per_weekend = weekend.groupby(weekend["datum"].map(week_key))
        counts = per_weekend.size().to_dict()
        for week, shifts_in_week in per_weekend:
            for _, row in shifts_in_week.iloc[MAX_WEEKEND_SHIFTS:].iterrows():
                add(row, f"More than {MAX_WEEKEND_SHIFTS} weekend shifts in week {week[1]}")
            if len(shifts_in_week) >= 2 and counts.get(_prev_week(week, -1), 0) >= 2:
                add(shifts_in_week.iloc[1], f"Two weekends in a row with 2 shifts (week {week[1]})")
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

    week_start_day = None  # maandag van de week die gecontroleerd wordt (zie hieronder)

    def may_work(name: str, weekday: int, dienst: str | None) -> bool:
        """Laten de persoonsregels deze weekdag (en dienst) toe? Dan geldt het minimum per week."""
        for r in person_rules:
            if r.naam != name:
                continue
            if r.regel == RULE_ONLY_DAYS and weekday not in parse_free_days(r.waarde):
                return False
            if r.regel == RULE_NEVER_DAYS and weekday in parse_free_days(r.waarde):
                return False
            if dienst and r.regel == RULE_ONLY_SHIFT and dienst.lower() not in _items(r.waarde):
                return False
            if dienst == EVENING and r.regel == RULE_EVENING_FROM and week_start_day is not None \
                    and week_start_day + timedelta(days=4) < date.fromisoformat(str(r.waarde)):  # nog geen doordeweekse avond
                return False
            if dienst and r.regel == RULE_NO_SHIFT and _shift_on_days(r.waarde) == (dienst.lower(), None):
                return False
            if dienst and r.regel == RULE_PREFERS and ":" not in str(r.waarde) and dienst.lower() != str(r.waarde).strip().lower():
                return False  # liever een andere dienst (bijv. Karel overdag): geen minimum voor deze dienst
        return True

    for name in {r.naam for r in person_rules if r.regel == RULE_ZERO}:
        for d, shift in (zero_hour_days(name, person_rules) or {}).items():
            if d in (period or []) and not _is_absent(name, d, absence_map) \
                    and not ((assignments["naam"] == name) & (assignments["datum"] == d)).any():
                issues.append({"datum": d, "naam": name, "dienst": shift,
                               "probleem": "Zero hours: agreed to work, but not scheduled"})

    for r in person_rules:
        if r.regel != RULE_ALWAYS or r.naam not in info or not info[r.naam].actief:
            continue
        shift, _, days = str(r.waarde).partition(":")
        for d in period or []:
            if (d.weekday() in parse_free_days(days) and not _is_absent(r.naam, d, absence_map)
                    and not ((assignments["naam"] == r.naam) & (assignments["datum"] == d)).any()):
                issues.append({"datum": d, "naam": r.naam, "dienst": shift.strip(),
                               "probleem": f"Always works {shift.strip()} on {WEEKDAYS[d.weekday()]}, but is not scheduled"})

    # Iedereen minimaal één doordeweekse avond en één weekendshift per volledige week (tenzij afwezig).
    for week in sorted({week_key(d) for d in period or []}):
        days = [d for d in period if week_key(d) == week]
        if len(days) < 7:
            continue
        in_week = assignments[assignments["datum"].map(week_key) == week]
        week_start_day = days[0]
        for person in team[team["actief"] & team["auto"]].itertuples():
            if any(_is_absent(person.naam, d, absence_map) for d in days):
                continue
            if zero_hour_days(person.naam, person_rules) is not None:
                continue  # nuluren: alleen op de afgesproken dagen
            mine = in_week[in_week["naam"] == person.naam]
            weekend = mine["datum"].map(is_weekend).astype(bool)
            if not weekend.any() and person.seniority != INTERN and may_work(person.naam, SATURDAY, None) | may_work(person.naam, SUNDAY, None):
                issues.append({"datum": None, "naam": person.naam, "dienst": "-",
                               "probleem": f"Week {week[1]}: no weekend shift"})
            if (not ((~weekend) & (mine["dienst"] == EVENING)).any() and person.seniority != INTERN
                    and not getattr(person, "geen_avond", False)
                    and any(may_work(person.naam, d, EVENING) for d in range(5))):
                issues.append({"datum": None, "naam": person.naam, "dienst": "-",
                               "probleem": f"Week {week[1]}: no weekday evening"})

    return pd.DataFrame(issues, columns=["datum", "naam", "dienst", "probleem"])
