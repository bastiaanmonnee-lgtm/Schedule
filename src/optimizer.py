"""Rooster maken met optimalisatie (Google OR-Tools, CP-SAT) in plaats van plek voor plek.

Alle weken worden in één keer bekeken, zodat het ene weekend niet de restjes van het andere krijgt.
Harde regels (één dienst per dag, rusttijd, max per week, weekendregel, kanalen, vrije dagen, persoonsregels)
gelden altijd; de rest weegt mee in een score:

    open plekken  >>  vaste afspraken (altijd werken, nuluren, UCL)  >  contracturen  >  weekminimum
    >  voorkeuren / afwisseling / extra mensen waar de wedstrijden zijn

Geeft None terug als OR-Tools er niet is of geen oplossing vindt; dan gebruikt scheduler.py de oude planner.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

import scheduler as sch

# Gewichten (hoger = belangrijker).
W_SHORT = {0: 1000, 1: 900, 2: 600, 3: 800}  # open plek: NL-sleuteldienst, weekendavond, weekenddag, doordeweeks
W_MUST = 700  # vaste afspraak niet gehaald (Always works, vaste dagen, nuluren, UCL-persoon)
W_CONTRACT = 120  # per dienst onder de contracturen
W_WEEK_MIN = 40  # geen doordeweekse avond / geen weekenddienst die week
W_WEEKEND_EXTRA = 200  # meer mensen in het weekend dan nodig
W_SECOND_WEEKEND = 20  # tweede weekenddienst in een week
W_REPEAT = 10  # zelfde weekenddienst (bijv. zaterdagavond) twee weken achter elkaar
W_PREF = 15  # dienst tegen de voorkeur in
TIME_LIMIT = 25  # seconden


def optimize_schedule(team, shifts, rules, calendar, absences, min_rest_hours=11, seed=0, existing=None,
                      person_rules=None) -> pd.DataFrame | None:
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return None

    shift_times = {r.dienst: (r.start, r.eind) for r in shifts.itertuples()}
    plan_shifts = [s for s in (sch.DAY, sch.EVENING) if s in shift_times]  # nacht: alleen met de hand
    days = sorted(calendar["datum"])
    if not days:
        return pd.DataFrame(columns=["datum", "dienst", "kanaal", "naam"])
    day_set = set(days)
    day_types = dict(zip(calendar["datum"], calendar["dagtype"]))
    games = {(d.datum, s): n for d in calendar.itertuples() for s, n in (getattr(d, "wedstrijden", None) or {}).items()}
    absence_map = sch._absence_map(absences)
    rules_df = person_rules if person_rules is not None else pd.DataFrame(columns=["naam", "regel", "waarde"])
    min_rest = timedelta(hours=min_rest_hours)

    people = team[team["actief"] & team["auto"]]
    info = {r.naam: r for r in people.itertuples()}
    names = list(info)
    weeks = sorted({sch.week_key(d) for d in days})

    # Wat al vastligt buiten de planperiode (bijv. de week ervoor): telt mee voor rust, per week en weekend.
    context = existing if existing is not None else pd.DataFrame(columns=["datum", "dienst", "kanaal", "naam"])
    context = context[~context["datum"].isin(day_set)]
    ctx_by_person = defaultdict(list)
    for r in context.itertuples():
        if r.dienst in shift_times:
            ctx_by_person[r.naam].append((r.datum, r.dienst))

    not_alone = {(r.naam, r.waarde) for r in rules_df.itertuples() if r.regel == sch.RULE_NOT_ALONE}
    together = {r.naam for r in rules_df.itertuples() if r.regel == sch.RULE_EVENING_TOGETHER}
    never_with = [(r.naam, r.waarde) for r in rules_df.itertuples() if r.regel == sch.RULE_NOT_WITH]

    def statically_ok(n, d, s, k) -> bool:
        p = info[n]
        if k not in sch.channels_of(p.kanalen):
            return False
        if p.vaste_dienst and p.vaste_dienst != s:
            return False
        if getattr(p, "geen_avond", False) and s == sch.EVENING:
            return False
        free = sch.parse_free_days(p.vrije_dagen) | ({sch.SATURDAY, sch.SUNDAY} if p.seniority == sch.INTERN else set())
        if d.weekday() in free:
            return False
        if sch._is_absent(n, d, absence_map, s):
            return False
        # Persoonsregels die niet van anderen afhangen (een dummy-collega op hetzelfde kanaal neutraliseert
        # "nooit alleen" en "alleen samen"; die staan hieronder als beperking).
        return not sch.rule_breaks(n, d, s, k, [("~", k)], rules_df)

    m = cp_model.CpModel()
    x = {}
    for n in names:
        for d in days:
            for s in plan_shifts:
                for k in sch.CHANNELS:
                    if statically_ok(n, d, s, k):
                        x[n, d, s, k] = m.NewBoolVar(f"x_{n}_{d}_{s}_{k}")

    def works(n, d, s=None):
        return [v for (nn, dd, ss, _), v in x.items() if nn == n and dd == d and (s is None or ss == s)]

    by_person_day = defaultdict(list)
    by_slot = defaultdict(list)  # (d, s, k) -> [(n, var)]
    for (n, d, s, k), v in x.items():
        by_person_day[n, d].append(v)
        by_slot[d, s, k].append((n, v))
    on_shift = {}
    for n in names:
        for d in days:
            for s in plan_shifts:
                vs = works(n, d, s)
                if vs:
                    var = m.NewBoolVar(f"on_{n}_{d}_{s}")
                    m.Add(sum(vs) == var)
                    on_shift[n, d, s] = var

    terms = []

    # --- harde regels ---------------------------------------------------------------
    for (n, d), vs in by_person_day.items():
        m.Add(sum(vs) <= 0 if d in {cd for cd, _ in ctx_by_person[n]} else sum(vs) <= 1)

    # Rusttijd: twee diensten (ook met wat al vastligt) die te dicht op elkaar zitten, niet allebei.
    windows = {(d, s): sch.shift_window(d, *shift_times[s]) for d in days + [days[0] - timedelta(days=1),
                                                                             days[-1] + timedelta(days=1)]
               for s in shift_times}

    def clash(a, b) -> bool:
        (s1, e1), (s2, e2) = a, b
        if s2 >= e1:
            return s2 - e1 < min_rest
        if s1 >= e2:
            return s1 - e2 < min_rest
        return True

    for n in names:
        for d in days:
            for s in plan_shifts:
                if (n, d, s) not in on_shift:
                    continue
                w = windows[d, s]
                nxt = d + timedelta(days=1)
                for s2 in plan_shifts:
                    if (n, nxt, s2) in on_shift and clash(w, windows[nxt, s2]):
                        m.Add(on_shift[n, d, s] + on_shift[n, nxt, s2] <= 1)
                for cd, cs in ctx_by_person[n]:
                    if abs((cd - d).days) <= 1 and clash(w, sch.shift_window(cd, *shift_times[cs])):
                        m.Add(on_shift[n, d, s] == 0)

    def weekend_vars(n, week):
        return [on_shift[n, d, s] for d in days for s in plan_shifts
                if (n, d, s) in on_shift and sch.week_key(d) == week and sch.is_weekend_shift(d, s)]

    def ctx_count(n, week, weekend=False):
        return sum(1 for cd, cs in ctx_by_person[n]
                   if sch.week_key(cd) == week and (not weekend or sch.is_weekend_shift(cd, cs)))

    all_weeks = sorted(set(weeks) | {sch.week_key(cd) for n in names for cd, _ in ctx_by_person[n]})
    two = {}
    for n in names:
        p = info[n]
        for week in all_weeks:
            wk_vars = [v for (nn, d, s), v in on_shift.items() if nn == n and sch.week_key(d) == week]
            already = ctx_count(n, week)
            if wk_vars:
                m.Add(sum(wk_vars) + already <= int(p.max_per_week))
            we = weekend_vars(n, week)
            ctx_we = ctx_count(n, week, weekend=True)
            t = m.NewBoolVar(f"two_{n}_{week}")
            m.Add(sum(we) + ctx_we <= 1 + t)
            if not we:
                m.Add(t == (1 if ctx_we >= 2 else 0))
            two[n, week] = t
            terms.append(W_SECOND_WEEKEND * t)
        for a, b in zip(all_weeks, all_weeks[1:]):
            if sch._prev_week(a, 1) == b:
                m.Add(two[n, a] + two[n, b] <= 1)  # na een weekend met 2 diensten is het volgende maximaal 1

    # Nooit samen in dezelfde dienst.
    for a, b in never_with:
        for d in days:
            for s in plan_shifts:
                if (a, d, s) in on_shift and (b, d, s) in on_shift:
                    m.Add(on_shift[a, d, s] + on_shift[b, d, s] <= 1)

    # Nooit alleen op een kanaal / 's avonds alleen samen: er moet nog iemand op hetzelfde kanaal staan.
    for (d, s, k), members in by_slot.items():
        for n, v in members:
            if (n, k) in not_alone or (n in together and s == sch.EVENING):
                others = [ov for on, ov in members if on != n]
                m.Add(v <= sum(others)) if others else m.Add(v == 0)

    # --- bezetting: open plekken zo min mogelijk ------------------------------------
    need = sch.requirements(calendar, rules)
    for r in need.itertuples():
        if r.dienst not in plan_shifts:
            continue
        counted = [v for n, v in by_slot.get((r.datum, r.dienst, r.kanaal), []) if (n, r.kanaal) not in not_alone]
        short = m.NewIntVar(0, int(r.nodig), f"short_{r.datum}_{r.dienst}_{r.kanaal}")
        m.Add(sum(counted) + short >= int(r.nodig))
        terms.append(W_SHORT[sch.priority(r.datum, r.dienst, r.kanaal)] * short)
        extra = m.NewIntVar(0, 20, f"extra_{r.datum}_{r.dienst}_{r.kanaal}")
        m.Add(extra >= sum(counted) - int(r.nodig))
        if sch.is_weekend(r.datum):
            terms.append(W_WEEKEND_EXTRA * extra)  # weekend: geen aanvulling tot contracturen
        else:
            terms.append(max(0, 3 - min(games.get((r.datum, r.dienst), 0), 3)) * extra)  # extra waar het druk is
    needed_keys = {(r.datum, r.dienst, r.kanaal) for r in need.itertuples()}
    for (d, s, k), members in by_slot.items():  # kanaal/dienst zonder vraag: liever niemand extra
        if (d, s, k) not in needed_keys:
            terms.extend((W_WEEKEND_EXTRA if sch.is_weekend(d) else 4) * v for n, v in members
                         if (n, k) not in not_alone)

    # --- vaste afspraken (zacht, zodat het rooster altijd lukt) ----------------------
    for n in names:
        p = info[n]
        agreed = sch.zero_hour_days(n, rules_df)
        always = sch.always_works(n, rules_df)
        fixed_days = sch.parse_free_days(p.vaste_dagen)
        for d in days:
            if sch._is_absent(n, d, absence_map):
                continue
            want = None
            if agreed is not None and d in agreed:
                want = None if agreed[d] == sch.ANY_SHIFT else agreed[d]
                must = True
            elif any(d.weekday() in ds for _, ds in always):
                want = next(s for s, ds in always if d.weekday() in ds)
                must = True
            elif p.ucl and day_types.get(d) == sch.UCL_DAY_TYPE:
                want, must = sch.EVENING, True
            elif d.weekday() in fixed_days:
                want, must = (p.vaste_dienst or None), True
            else:
                must = False
            if must:
                vs = works(n, d, want)
                miss = m.NewBoolVar(f"miss_{n}_{d}")
                m.Add(sum(vs) + miss >= 1)
                terms.append(W_MUST * miss)

    # --- contracturen, weekminimum, voorkeuren, afwisseling --------------------------
    for n in names:
        p = info[n]
        zero = sch.zero_hour_days(n, rules_df) is not None
        contract = int(getattr(p, "contracturen", 0) or 0)
        target = 0 if zero else (min(int(p.max_per_week), round(contract / sch.SHIFT_HOURS)) if contract
                                 else int(p.max_per_week))
        for week in weeks:
            wdays = [d for d in days if sch.week_key(d) == week]
            available = [d for d in wdays if not sch._is_absent(n, d, absence_map)]
            weekdays_planned = sum(1 for d in wdays if not sch.is_weekend(d))
            goal = min(target, ctx_count(n, week) + round(target * weekdays_planned / 5), len(available) + ctx_count(n, week))
            wk = [v for (nn, d, s), v in on_shift.items() if nn == n and sch.week_key(d) == week]
            if goal > 0:
                under = m.NewIntVar(0, goal, f"under_{n}_{week}")
                m.Add(sum(wk) + ctx_count(n, week) + under >= goal)
                terms.append(W_CONTRACT * under)
            full_week = len(wdays) == 7 and len(available) == 7
            if full_week and not zero and p.seniority != sch.INTERN:
                we = [on_shift[n, d, s] for d in wdays for s in plan_shifts if (n, d, s) in on_shift and sch.is_weekend(d)]
                if we:
                    no_we = m.NewBoolVar(f"nowe_{n}_{week}")
                    m.Add(sum(we) + no_we >= 1)
                    terms.append(W_WEEK_MIN * no_we)
                ev = [on_shift[n, d, sch.EVENING] for d in wdays if (n, d, sch.EVENING) in on_shift and not sch.is_weekend(d)]
                prefers_day = sch.preferred_shift(n, wdays[0], rules_df) == sch.DAY
                if ev and not prefers_day:
                    no_ev = m.NewBoolVar(f"noev_{n}_{week}")
                    m.Add(sum(ev) + no_ev >= 1)
                    terms.append(W_WEEK_MIN * no_ev)
        for (nn, d, s), v in on_shift.items():
            if nn != n:
                continue
            pref = sch.preferred_shift(n, d, rules_df)
            if pref and pref != s:
                terms.append(W_PREF * v)
            if sch.is_weekend(d):  # zelfde weekenddienst niet twee weken achter elkaar
                nd = d + timedelta(days=7)
                if (n, nd, s) in on_shift:
                    rep = m.NewBoolVar(f"rep_{n}_{d}_{s}")
                    m.Add(v + on_shift[n, nd, s] <= 1 + rep)
                    terms.append(W_REPEAT * rep)

    m.Minimize(sum(terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = TIME_LIMIT
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = int(seed)
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    rows = [{"datum": d, "dienst": s, "kanaal": k, "naam": n} for (n, d, s, k), v in x.items() if solver.Value(v)]
    return pd.DataFrame(rows, columns=["datum", "dienst", "kanaal", "naam"])
