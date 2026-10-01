"""Schedule tool for the social media team (football).

Start met:  streamlit run src/main.py   (of: docker compose up)
"""

from __future__ import annotations

import io
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import calendar_view
import logos
import matches
import scheduler as sch
import storage

st.set_page_config(page_title="Schedule · 433", page_icon="⚽", layout="wide", initial_sidebar_state="collapsed")

# Zwarte tekst op de neon-gele labels (bijv. de gekozen kanalen), anders is wit op geel onleesbaar.
st.markdown(
    """<style>
    [data-baseweb="tag"], [data-baseweb="tag"] * { color: #000 !important; }
    [data-baseweb="tag"] svg { fill: #000 !important; }
    </style>""",
    unsafe_allow_html=True,
)


# --- helpers voor bewerkbare tabellen ------------------------------------------------
# De invoer van st.data_editor blijft binnen een sessie gelijk (anders gaan bewerkingen
# verloren); de uitvoer wordt bij elke wijziging direct naar schijf geschreven.

def base(name: str, loader):
    if f"{name}_base" not in st.session_state:
        st.session_state[f"{name}_base"] = loader()
        st.session_state[f"{name}_ver"] = 0
    return st.session_state[f"{name}_base"]


def reset_base(name: str, df: pd.DataFrame) -> None:
    st.session_state[f"{name}_base"] = df
    st.session_state[f"{name}_ver"] = st.session_state.get(f"{name}_ver", 0) + 1


def editor_key(name: str) -> str:
    return f"{name}_editor_{st.session_state.get(f'{name}_ver', 0)}"


def persist(name: str, df: pd.DataFrame) -> None:
    last = st.session_state.get(f"{name}_saved")
    if last is None or not df.equals(last):
        storage.save(name, df)
        st.session_state[f"{name}_saved"] = df.copy()


def merge_range(full: pd.DataFrame, part: pd.DataFrame, dates: list[date]) -> pd.DataFrame:
    """Vervang in `full` alle rijen binnen `dates` door `part`."""
    kept = full[~full["datum"].isin(dates)]
    return pd.concat([kept, part[full.columns]], ignore_index=True).sort_values("datum").reset_index(drop=True)


def day_label(d: date) -> str:
    return f"{sch.WEEKDAYS[d.weekday()]} {d:%d-%m}"


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


DATE = st.column_config.DateColumn("Date", format="DD-MM-YYYY")
CHANNEL_OPTIONS = [sch.CHANNEL_LABELS[c] for c in sch.CHANNELS]

COVERAGE_LABELS = {
    "datum": "Date", "dagtype": "Day type", "dienst": "Shift", "kanaal": "Channel", "nodig": "Needed",
    "ingepland": "Scheduled", "status": "Status", "mensen": "People", "reden": "Extra for",
}
CONFLICT_LABELS = {"datum": "Date", "naam": "Name", "dienst": "Shift", "probleem": "Issue"}

# --- periode (sidebar) ---------------------------------------------------------------

with st.sidebar:
    st.header("⚽ Period")
    today = date.today()
    start = monday(st.date_input("From week of", value=monday(today) + timedelta(days=7), format="DD-MM-YYYY"))
    weeks = st.number_input("Number of weeks", min_value=1, max_value=26, value=4, step=1)
    period = [start + timedelta(days=i) for i in range(int(weeks) * 7)]
    end = period[-1]
    period_key = (start, int(weeks))
    st.caption(f"Week {start.isocalendar()[1]} to {end.isocalendar()[1]}  \n{start:%d-%m-%Y} – {end:%d-%m-%Y}")
    st.divider()
    st.caption("All changes are saved automatically in the `data/` folder.")

# --- wedstrijden laden (nodig voor dagtypes, extra bezetting en de kalender) ---------

@st.cache_data(ttl=3600, show_spinner="Loading matches…")
def load_matches(first: date, last: date, clubs: tuple[str, ...]) -> pd.DataFrame:
    return matches.fetch_matches(first, last, clubs)


@st.cache_data(ttl=3600, show_spinner="Loading logos…")
def load_logo_urls(ids: tuple[int, ...]) -> dict[int, str]:
    return logos.logo_urls(ids)  # links zijn langer geldig (logos.LINK_HOURS) dan de cache


@st.cache_data(ttl=3600, show_spinner=False)
def load_league_logo_urls(assets: tuple[str, ...]) -> dict[str, str]:
    return logos.league_logo_urls(assets)


clubs = matches.DEFAULT_CLUBS
games, match_error = None, None
try:
    found = load_matches(start, end, tuple(clubs))
    games = matches.for_teams(found, matches.default_teams(matches.team_names(found, clubs), clubs))
except Exception as exc:  # database niet bereikbaar, .env niet ingevuld, ...
    match_error = exc

ucl_days, big_by_day = set(), {}
if games is not None:
    ucl_days = {g.datum for g in games.itertuples()
                if (calendar_view.competition_of(g.competitie) or ("",))[0] == "ucl"}
    for g in matches.big_matches(games, clubs, sch.TOP_CLUBS):
        big_by_day.setdefault(g.datum, []).append((f"{g.HomeTeam} – {g.AwayTeam}", sch.shift_for_kickoff(g.tijd)))

t_matches, t_events, t_team, t_weeks, t_rules, t_absence, t_roster = st.tabs(
    ["⚽ Matches", "⭐ Special events", "👥 Employees", "📅 Weeks & staffing", "⚙️ Staffing rules", "🏖️ Time off", "🗓️ Schedule"]
)

# --- Medewerkers ---------------------------------------------------------------------

with t_team:
    st.subheader("Employees")
    st.caption(
        "**Channels**: where someone can work. **Contract hours**: compared per week in the match calendar. "
        "**Always shift / Always on**: e.g. only evenings and always on `fri`. "
        "**UCL nights**: always on main during Champions League evenings. Days like `mon, wed`."
    )
    # clean_team: ook een sessie van vóór een nieuwe kolom (bijv. contracturen) krijgt die kolom te zien.
    team_view = storage.clean_team(base("employees", storage.load_team)).assign(
        kanalen=lambda df: df["kanalen"].map(lambda v: [sch.CHANNEL_LABELS[c] for c in sch.channels_of(v)])
    )
    team = st.data_editor(
        team_view,
        key=editor_key("employees"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_order=["naam", "kanalen", "contracturen", "auto", "max_per_week", "vaste_dienst", "vaste_dagen",
                      "ucl", "vrije_dagen", "seniority", "actief", "notitie"],
        column_config={
            "naam": st.column_config.TextColumn("Name", required=True),
            "kanalen": st.column_config.MultiselectColumn("Channels", options=CHANNEL_OPTIONS),
            "contracturen": st.column_config.NumberColumn(
                "Contract hours / week", min_value=0, max_value=60, step=1, default=0,
                help="Shown next to each week in the match calendar: red = fewer hours scheduled, orange = more.",
            ),
            "auto": st.column_config.CheckboxColumn(
                "Auto schedule", default=True, help="Off: the planner skips this person; you can still add them by hand.",
            ),
            "max_per_week": st.column_config.NumberColumn("Max shifts / week", min_value=0, max_value=7, step=1, default=5),
            "vaste_dienst": st.column_config.SelectboxColumn("Always shift", options=["", sch.DAY, sch.EVENING]),
            "vaste_dagen": st.column_config.TextColumn("Always on"),
            "ucl": st.column_config.CheckboxColumn("UCL nights", default=False),
            "vrije_dagen": st.column_config.TextColumn("Fixed days off"),
            "seniority": st.column_config.SelectboxColumn("Seniority", options=sch.SENIORITY_LEVELS, default="Medior"),
            "actief": st.column_config.CheckboxColumn("Active", default=True),
            "notitie": st.column_config.TextColumn("Note"),
        },
    )
    team = storage.clean_team(team)
    persist("employees", team)

    active = team[team["actief"]]
    cols = st.columns(len(sch.CHANNELS) + 1)
    cols[0].metric("Active", len(active))
    for col, channel in zip(cols[1:], sch.CHANNELS):
        col.metric(sch.CHANNEL_LABELS[channel], int(active["kanalen"].map(lambda v: channel in sch.channels_of(v)).sum()))

    st.subheader("Other team per month")
    st.caption("People who work for another team (e.g. WomenFC) in a month are not scheduled here that month.")
    months = sorted({f"{d:%Y-%m}" for d in period} | {f"{(today.replace(day=1) + timedelta(days=32 * i)):%Y-%m}" for i in range(12)})
    other_team = st.data_editor(
        base("other_team", storage.load_other_team),
        key=editor_key("other_team"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "naam": st.column_config.SelectboxColumn("Name", options=list(team["naam"]), required=True),
            "maand": st.column_config.SelectboxColumn("Month", options=sorted(set(months) | set(base("other_team", storage.load_other_team)["maand"])), required=True),
            "team": st.column_config.TextColumn("Team", default="WomenFC"),
        },
    )
    other_team = storage.clean_other_team(other_team)
    persist("other_team", other_team)

# --- Bezettingsregels ----------------------------------------------------------------

with t_rules:
    left, right = st.columns([1, 2])
    with left:
        st.subheader("Shifts")
        st.caption("Times as `HH:MM`. An end time before the start time means past midnight.")
        shifts = st.data_editor(
            base("shifts", storage.load_shifts),
            key=editor_key("shifts"),
            hide_index=True,
            width="stretch",
            disabled=["dienst"],
            column_config={
                "dienst": st.column_config.TextColumn("Shift"),
                "start": st.column_config.TextColumn("Start", validate=r"^\d{1,2}:\d{2}$"),
                "eind": st.column_config.TextColumn("End", validate=r"^\d{1,2}:\d{2}$"),
            },
        )
        shifts = storage.clean_shifts(shifts)
        persist("shifts", shifts)
        shift_names = list(shifts["dienst"])

    with right:
        st.subheader("Staffing per day type")
        st.caption(
            "People needed per channel. Day types are set automatically: *Champions League* on UCL nights, "
            "*Weekend* on Sat/Sun, otherwise *Regular day*. Main on UCL nights counts the UCL person (Rogier). "
            f"Big matches (two clubs from the top {sch.TOP_CLUBS} in clubs.py) and special events get "
            "+1 on main and app in the shift of the match (events: evening)."
        )
        rules = st.data_editor(
            base("staffing", storage.load_rules),
            key=editor_key("staffing"),
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_config={
                "dagtype": st.column_config.TextColumn("Day type", required=True),
                "dienst": st.column_config.SelectboxColumn("Shift", options=shift_names, required=True),
                **{c: st.column_config.NumberColumn(sch.CHANNEL_LABELS[c], min_value=0, max_value=10, step=1, default=1)
                   for c in sch.CHANNELS},
            },
        )
        rules = storage.clean_rules(rules)
        persist("staffing", rules)

    day_types = list(dict.fromkeys(rules["dagtype"]))

    st.subheader("Settings")
    settings = storage.load_settings()
    new_settings = {
        **settings,
        "min_rust_uren": st.number_input(
            "Minimum rest between shifts (hours)", 0, 24, int(settings["min_rust_uren"]),
            help="With 11 hours nobody works a day shift right after an evening shift.",
        ),
    }
    if new_settings != settings:
        storage.save_settings(new_settings)
    settings = new_settings

# --- Special events ------------------------------------------------------------------

with t_events:
    st.subheader("Special events")
    st.caption("Events show up on their day in the match calendar and get extra staffing (+1 main and app, evening).")
    events = st.data_editor(
        base("events", storage.load_events),
        key=editor_key("events"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "titel": st.column_config.TextColumn("Title", required=True),
            "datum": st.column_config.DateColumn("Day", format="DD-MM-YYYY", required=True),
        },
    )
    events = storage.clean_events(events)
    persist("events", events)
    events_by_day = events.groupby("datum")["titel"].apply(list).to_dict()

# --- Afwezigheid ---------------------------------------------------------------------

with t_absence:
    st.subheader("Time off & holidays")
    st.caption("People who are absent in this period are not scheduled.")
    absences = st.data_editor(
        base("afwezigheid", storage.load_absences),
        key=editor_key("afwezigheid"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "naam": st.column_config.SelectboxColumn("Name", options=list(team["naam"]), required=True),
            "van": st.column_config.DateColumn("From", format="DD-MM-YYYY", required=True),
            "tot": st.column_config.DateColumn("Until (incl.)", format="DD-MM-YYYY"),
            "reden": st.column_config.TextColumn("Reason"),
        },
    )
    absences = storage.clean_absences(absences)
    persist("afwezigheid", absences)

# Voor de planning: vrij/afwezig plus de maanden bij een ander team (WomenFC).
unavailable = pd.concat([absences, storage.other_team_as_absences(other_team)], ignore_index=True)

# --- Weken & bezetting ---------------------------------------------------------------

def extra_for(d: date) -> tuple[str, str]:
    """Extra bezetting: (reden, dienst) voor events en belangrijke wedstrijden op deze dag."""
    reasons = list(events_by_day.get(d, [])) + [label for label, _ in big_by_day.get(d, [])]
    if not reasons:
        return "", ""
    shifts_needed = [sch.EVENING] * bool(events_by_day.get(d)) + [s for _, s in big_by_day.get(d, [])]
    return " / ".join(reasons), sch.EVENING if sch.EVENING in shifts_needed else shifts_needed[0]


def build_period_calendar(days: pd.DataFrame) -> pd.DataFrame:
    stored = days.set_index("datum")
    rows = []
    for d in period:
        manual = stored.at[d, "handmatig"] if d in stored.index else ""
        reason, extra_shift = extra_for(d)
        rows.append({
            "datum": d,
            "week": d.isocalendar()[1],
            "dag": sch.WEEKDAYS[d.weekday()],
            "auto": sch.auto_day_type(d, ucl_days),
            "handmatig": manual if manual in day_types else "",
            "extra": reason,
            "extra_dienst": extra_shift,
            "notitie": stored.at[d, "notitie"] if d in stored.index else "",
        })
    return pd.DataFrame(rows)


with t_weeks:
    st.subheader("Weeks & staffing")
    st.caption(
        "The day type is set automatically from the matches; pick an *Override* to change it. "
        "Extra staffing comes from big matches and special events."
    )
    if match_error is not None:
        st.warning(f"Matches could not be loaded, so UCL nights and big matches are not detected: {match_error}")
    all_days = storage.load_days()
    edited = st.data_editor(
        build_period_calendar(all_days),
        key=f"days_editor_{period_key}",
        hide_index=True,
        width="stretch",
        height=min(38 + 35 * len(period), 600),
        disabled=["datum", "week", "dag", "auto", "extra"],
        column_order=["datum", "week", "dag", "auto", "handmatig", "extra", "notitie"],
        column_config={
            "datum": DATE,
            "week": st.column_config.NumberColumn("Week"),
            "dag": st.column_config.TextColumn("Day"),
            "auto": st.column_config.TextColumn("Automatic"),
            "handmatig": st.column_config.SelectboxColumn("Override", options=[""] + day_types),
            "extra": st.column_config.TextColumn("Extra staffing for", width="large"),
            "notitie": st.column_config.TextColumn("Note", width="medium"),
        },
    )
    edited["handmatig"] = edited["handmatig"].fillna("")
    edited["notitie"] = edited["notitie"].fillna("")
    persist("days", merge_range(all_days, edited, period))
    calendar = edited.assign(dagtype=edited["handmatig"].where(edited["handmatig"] != "", edited["auto"]))

    # Benodigde bezetting per week vs. beschikbare capaciteit.
    need = sch.requirements(calendar, rules)
    per_week = need.assign(week=need["datum"].map(lambda d: d.isocalendar()[1])).groupby("week")["nodig"].sum()
    per_week = per_week.reindex(sorted(calendar["week"].unique()), fill_value=0).to_frame()
    capacity = {}
    for week, days in calendar.groupby("week")["datum"]:
        cap = 0
        for p in active.itertuples():
            free = sch.parse_free_days(p.vrije_dagen)
            available = sum(
                d.weekday() not in free
                and not ((unavailable["naam"] == p.naam) & (unavailable["van"] <= d) & (unavailable["tot"] >= d)).any()
                for d in days
            )
            cap += min(p.max_per_week, available)
        capacity[week] = cap
    per_week["capaciteit"] = pd.Series(capacity)
    per_week["marge"] = per_week["capaciteit"] - per_week["nodig"]
    st.subheader("Per week")
    st.dataframe(
        per_week.reset_index().astype(int),
        hide_index=True,
        width="stretch",
        column_config={
            "week": "Week",
            "nodig": "Shifts needed",
            "capaciteit": st.column_config.NumberColumn("Team capacity", help="Sum of max shifts, taking time off into account"),
            "marge": "Margin",
        },
    )

# --- Rooster -------------------------------------------------------------------------

def neighbours(full: pd.DataFrame) -> pd.DataFrame:
    """Diensten vlak voor en na de periode: die tellen mee voor de rusttijd."""
    edge = {start - timedelta(days=1), end + timedelta(days=1)}
    return full[full["datum"].isin(edge)]


def plan(full: pd.DataFrame, keep: pd.DataFrame | None = None, variant: int | None = None) -> pd.DataFrame:
    """Roostervoorstel voor de periode dat nergens tegen een regel ingaat.

    `keep`: diensten die moeten blijven staan (bij Fix); de planner vult alleen de rest aan.
    """
    context = neighbours(full)
    existing = pd.concat([context, keep], ignore_index=True) if keep is not None else context
    proposal = sch.generate_schedule(team, shifts, rules, calendar, unavailable, settings["min_rust_uren"],
                                     int(seed if variant is None else variant), existing=existing)
    # Vangnet: wat toch zou botsen (bijv. met een dienst vlak voor de periode) gaat eruit.
    return sch.drop_conflicts(proposal, team, shifts, unavailable, settings["min_rust_uren"], context)


def to_excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:31], index=not isinstance(df.index, pd.RangeIndex))
            ws = writer.sheets[name[:31]]
            for column in ws.columns:
                width = max(len(str(c.value or "")) for c in column)
                ws.column_dimensions[column[0].column_letter].width = min(max(width + 2, 8), 50)
    return buffer.getvalue()


STATUS_COLORS = {
    sch.STATUS_OK: "background-color: #d9f2e3",
    sch.STATUS_SHORT: "background-color: #f8d0d0",
    sch.STATUS_OVER: "background-color: #dfe7fb",
}


def styled_status(df: pd.DataFrame):
    return df.rename(columns=COVERAGE_LABELS).style.map(lambda v: STATUS_COLORS.get(v, ""), subset=["Status"])


with t_roster:
    st.subheader("Schedule")
    full_roster = storage.load_roster()

    g1, g2, g3 = st.columns([1, 1, 3], vertical_alignment="bottom")
    seed = g1.number_input("Variant", min_value=0, value=0, step=1, help="Different number = different proposal")
    if g2.button("✨ Generate proposal", type="primary"):
        proposal = plan(full_roster)
        persist("schedule", storage.clean_roster(merge_range(full_roster, proposal, period)))
        reset_base("schedule", storage.clean_roster(proposal))
        st.session_state["schedule_period"] = period_key
        st.rerun()
    g3.caption(
        "Fixed arrangements first (UCL nights, fixed days), then the key NL shifts (Fri/Sat evening, Sun day), "
        "the weekend, evenings and day shifts. Everyone gets at least one weekday evening and one weekend shift "
        "where possible, and weekend shifts rotate. A new proposal overwrites the schedule for this period."
    )

    if st.session_state.get("schedule_period") != period_key:
        st.session_state["schedule_period"] = period_key
        reset_base("schedule", full_roster[full_roster["datum"].isin(period)].reset_index(drop=True))

    with st.expander("✏️ Edit manually", expanded=False):
        st.caption("Add or remove rows; the checks below and the match calendar update immediately.")
        roster = st.data_editor(
            st.session_state["schedule_base"],
            key=editor_key("schedule"),
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            height=400,
            column_config={
                "datum": st.column_config.DateColumn("Date", format="DD-MM-YYYY", min_value=start, max_value=end, required=True),
                "dienst": st.column_config.SelectboxColumn("Shift", options=shift_names, required=True),
                "kanaal": st.column_config.SelectboxColumn("Channel", options=sch.CHANNELS, required=True),
                "naam": st.column_config.SelectboxColumn("Name", options=list(team["naam"]), required=True),
            },
        )
    roster = storage.clean_roster(roster)
    persist("schedule", storage.clean_roster(merge_range(full_roster, roster, period)))

    if roster.empty:
        st.info("No schedule for this period yet. Click **Generate proposal**.")
    else:
        coverage = sch.check_coverage(roster, rules, calendar)
        conflicts = sch.check_conflicts(roster, team, shifts, unavailable, settings["min_rust_uren"], period)
        # Extra mensen (bijv. om aan de contracturen te komen) zijn geen probleem; alleen tekorten tonen.
        problems = coverage[coverage["status"] == sch.STATUS_SHORT]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Shifts scheduled", len(roster))
        m2.metric("Slots OK", f"{(coverage['status'] == sch.STATUS_OK).sum()} / {len(coverage)}")
        m3.metric("Shortages", int((coverage["status"] == sch.STATUS_SHORT).sum()))
        m4.metric("Rule conflicts", len(conflicts))

        columns = [f"{s} · {sch.CHANNEL_LABELS[c]}" for s in shift_names for c in sch.CHANNELS]
        per_day = (
            roster.assign(kolom=roster["dienst"] + " · " + roster["kanaal"].map(sch.CHANNEL_LABELS))
            .groupby(["datum", "kolom"])["naam"].apply(lambda x: ", ".join(sorted(x))).unstack("kolom")
            .reindex(columns=columns)
        )
        per_day = calendar.set_index("datum")[["dagtype", "extra"]].join(per_day).fillna("")
        for row in coverage[coverage["status"] == sch.STATUS_SHORT].itertuples():
            col = f"{row.dienst} · {row.kanaal}"
            if col in per_day.columns:
                per_day.at[row.datum, col] = f"{per_day.at[row.datum, col]}  ⚠ {row.nodig - row.ingepland} short".strip()
        per_day.index = [day_label(d) for d in per_day.index]
        per_day = per_day.rename(columns={"dagtype": "Day type", "extra": "Extra for"})

        per_person = roster.assign(
            dag=roster["datum"].map(day_label),
            cel=roster["dienst"] + " (" + roster["kanaal"].map(sch.CHANNEL_LABELS) + ")",
        ).pivot_table(index="naam", columns="dag", values="cel", aggfunc=lambda x: " + ".join(x))
        per_person = per_person.reindex(
            index=[n for n in team["naam"] if n in per_person.index] + [n for n in per_person.index if n not in set(team["naam"])],
            columns=[day_label(d) for d in period],
        ).fillna("")
        per_person.index.name = "Name"
        per_person.columns.name = None

        weekend = roster["datum"].map(sch.is_weekend)
        summary = pd.crosstab(roster["naam"], roster["dienst"]).reindex(columns=shift_names, fill_value=0)
        summary = summary.join(
            pd.crosstab(roster["naam"], roster["kanaal"]).reindex(columns=sch.CHANNELS, fill_value=0)
            .rename(columns=sch.CHANNEL_LABELS)
        )
        summary.insert(0, "Total", summary[shift_names].sum(axis=1))
        summary.insert(1, "Weekend", roster[weekend].groupby("naam").size())
        summary.insert(2, "Weekday evenings", roster[~weekend & (roster["dienst"] == sch.EVENING)].groupby("naam").size())
        summary.insert(3, "Sat evening", roster[(roster["datum"].map(lambda d: d.weekday()) == sch.SATURDAY)
                                               & (roster["dienst"] == sch.EVENING)].groupby("naam").size())
        summary = team.set_index("naam")[["max_per_week"]].join(summary, how="left").fillna(0).astype(int)
        summary["Avg. per week"] = (summary["Total"] / int(weeks)).round(1)
        summary = summary.rename(columns={"max_per_week": "Max / week"})
        summary.index.name = "Name"

        v_day, v_person, v_check, v_split = st.tabs(["Per day", "Per person", "Staffing check", "Distribution"])
        with v_day:
            st.dataframe(per_day, width="stretch", height=min(38 + 35 * len(per_day), 800))
        with v_person:
            st.dataframe(per_person, width="stretch")
        with v_check:
            if problems.empty and conflicts.empty:
                st.success("All shifts are properly staffed and there are no conflicts. 🎉")
            if not problems.empty:
                st.markdown("**Staffing issues**")
                st.dataframe(styled_status(problems), hide_index=True, width="stretch", column_config={"Date": DATE})
            if not conflicts.empty:
                st.markdown("**Rule conflicts**")
                st.dataframe(conflicts.rename(columns=CONFLICT_LABELS), hide_index=True, width="stretch",
                             column_config={"Date": DATE})
            with st.expander("All shifts"):
                st.dataframe(styled_status(coverage), hide_index=True, width="stretch", column_config={"Date": DATE})
        with v_split:
            st.caption("How the shifts are spread across the team.")
            st.dataframe(summary, width="stretch")

        st.download_button(
            "⬇️ Download as Excel",
            data=to_excel({
                "Schedule per day": per_day,
                "Per person": per_person,
                "Distribution": summary,
                "Staffing": coverage.rename(columns=COVERAGE_LABELS),
                "Conflicts": conflicts.rename(columns=CONFLICT_LABELS),
            }),
            file_name=f"schedule_week{start.isocalendar()[1]}-{end.isocalendar()[1]}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# --- Wedstrijdkalender (als laatste: dan staat het rooster van deze run er al in) ----
# Een eigen component, zodat je mensen naar een andere dienst kunt slepen: de browser stuurt
# de verplaatsing terug naar Python, die het rooster aanpast en opslaat.

CALENDAR_JS = """
export default function({ data, parentElement, setTriggerValue }) {
  let root = parentElement.querySelector(".mc-root");
  if (!root) {
    root = document.createElement("div");
    root.className = "mc-root";
    parentElement.appendChild(root);
  }
  root.innerHTML = data.html;

  root.querySelectorAll(".mc-x").forEach((x) => {
    x.addEventListener("click", (e) => {
      e.stopPropagation();
      const chip = x.closest(".mc-chip");
      setTriggerValue("move", { name: chip.dataset.name, from_date: chip.dataset.date, from_shift: chip.dataset.shift,
                                channel: chip.dataset.channel, to_date: null, to_shift: null });
    });
  });

  root.querySelectorAll(".mc-add").forEach((plus) => {
    plus.addEventListener("click", () => {
      const row = plus.closest("[data-drop-date]");
      // Naam typen (met suggesties) en een kanaal kiezen; Enter voegt toe, Esc annuleert.
      const box = document.createElement("span");
      box.className = "mc-picker-box";
      const picker = document.createElement("input");
      picker.className = "mc-picker";
      picker.placeholder = "Type a name…";
      const list = document.createElement("datalist");
      list.id = "mc-people-" + Math.random().toString(36).slice(2);
      list.innerHTML = data.people.map((n) => `<option value="${n}"></option>`).join("");
      picker.setAttribute("list", list.id);
      const channel = document.createElement("select");
      channel.className = "mc-picker";
      channel.innerHTML = '<option value="">Auto</option>' +
        Object.entries(data.channels).map(([c, label]) => `<option value="${c}">${label}</option>`).join("");
      box.append(picker, channel, list);
      const match = () => {
        const typed = picker.value.trim().toLowerCase();
        if (!typed) return null;
        const exact = data.people.find((n) => n.toLowerCase() === typed);
        const starts = data.people.filter((n) => n.toLowerCase().startsWith(typed));
        return exact || (starts.length === 1 ? starts[0] : null);
      };
      const add = () => {
        const name = match();
        if (!name) { picker.style.borderColor = "#ff5a5a"; picker.focus(); return; }
        setTriggerValue("move", { name, from_date: null, from_shift: null, channel: channel.value || null,
                                  to_date: row.dataset.dropDate, to_shift: row.dataset.dropShift });
      };
      const close = () => box.replaceWith(plus);
      box.addEventListener("keydown", (e) => {
        if (e.key === "Enter") add();
        if (e.key === "Escape") close();
      });
      // Naam en kanaal ingevuld: meteen in de dienst zetten.
      channel.addEventListener("change", () => { if (match()) add(); else picker.focus(); });
      picker.addEventListener("change", () => { if (match() && channel.value) add(); });
      // Klik buiten het vakje: sluiten (niet als je van naam naar kanaal gaat).
      box.addEventListener("focusout", () => setTimeout(() => { if (!box.matches(":focus-within")) close(); }, 200));
      plus.replaceWith(box);
      picker.focus();
    });
  });

  // Op het kanaal-label (M / A / NL) klikken: kanaal van die persoon in die dienst wijzigen.
  root.querySelectorAll(".mc-chip[data-date] > i").forEach((label) => {
    label.title = "Change channel";
    label.addEventListener("click", (e) => {
      e.stopPropagation();
      const chip = label.closest(".mc-chip");
      const select = document.createElement("select");
      select.className = "mc-picker";
      select.innerHTML = Object.entries(data.channels)
        .map(([c, l]) => `<option value="${c}" ${c === chip.dataset.channel ? "selected" : ""}>${l}</option>`).join("");
      select.addEventListener("change", () => setTriggerValue("move", {
        name: chip.dataset.name, from_date: chip.dataset.date, from_shift: chip.dataset.shift, channel: chip.dataset.channel,
        to_date: chip.dataset.date, to_shift: chip.dataset.shift, new_channel: select.value }));
      select.addEventListener("blur", () => select.replaceWith(label));
      label.replaceWith(select);
      select.focus();
    });
  });
  let dragged = null;
  root.querySelectorAll('.mc-chip[draggable="true"]').forEach((chip) => {
    chip.addEventListener("dragstart", (e) => {
      dragged = { ...chip.dataset };
      chip.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", chip.dataset.name);
    });
    chip.addEventListener("dragend", () => chip.classList.remove("dragging"));
  });

  root.querySelectorAll("[data-drop-date]").forEach((zone) => {
    // Binnenste vak wint: een dienst-rij binnen een dag vangt de drop, anders de hele dag.
    zone.addEventListener("dragover", (e) => {
      if (!dragged) return;
      e.preventDefault();
      e.stopPropagation();
      root.querySelectorAll(".over").forEach((z) => { if (z !== zone) z.classList.remove("over"); });
      zone.classList.add("over");
    });
    zone.addEventListener("dragleave", (e) => { if (!zone.contains(e.relatedTarget)) zone.classList.remove("over"); });
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.remove("over");
      if (!dragged) return;
      const move = {
        name: dragged.name,
        from_date: dragged.date || null, from_shift: dragged.shift || null, channel: dragged.channel || null,
        // Op de dag zelf (niet op een dienst) laten vallen: dezelfde dienst als waar de persoon vandaan komt.
        to_date: zone.dataset.dropDate || null,
        to_shift: zone.dataset.dropDate ? (zone.dataset.dropShift || dragged.shift || "Day") : null,
      };
      dragged = null;
      if (!move.to_date) return;
      if (move.to_date === move.from_date && move.to_shift === move.from_shift) return;  // zelfde dienst
      setTriggerValue("move", move);
    });
  });
}
"""
match_calendar = st.components.v2.component("match_calendar", js=CALENDAR_JS)


def pick_channel(name: str, day: date, dienst: str) -> str:
    """Kanaal voor iemand die vanuit de balk op een dienst wordt gezet: waar nog iemand ontbreekt."""
    person = team.set_index("naam")["kanalen"].get(name, "")
    options = sch.channels_of(person) or ["main"]
    need = sch.requirements(calendar[calendar["datum"] == day], rules)
    for channel in options:
        needed = need[(need["dienst"] == dienst) & (need["kanaal"] == channel)]["nodig"].sum()
        planned = ((roster["datum"] == day) & (roster["dienst"] == dienst) & (roster["kanaal"] == channel)).sum()
        if planned < needed:
            return channel
    return options[0]


def apply_move(move: dict) -> tuple[str, bool]:
    """Verplaats (of verwijder) iemand in het rooster en sla het op.

    Geeft (melding, gelukt) terug. Mag het niet (te weinig rust, al ingepland, afwezig, ander kanaal ...),
    dan blijft het rooster zoals het was.
    """
    updated = roster.copy()
    name = move["name"]
    if move.get("new_channel"):
        day = date.fromisoformat(move["from_date"])
        row = ((updated["datum"] == day) & (updated["dienst"] == move["from_shift"])
               & (updated["kanaal"] == move["channel"]) & (updated["naam"] == name))
        if move["new_channel"] not in sch.channels_of(team.set_index("naam")["kanalen"].get(name, "")):
            return f"Not possible: {name} does not work on {sch.CHANNEL_LABELS.get(move['new_channel'], move['new_channel'])}", False
        updated.loc[updated[row].index[:1], "kanaal"] = move["new_channel"]
        updated = storage.clean_roster(updated.drop_duplicates())
        persist("schedule", storage.clean_roster(merge_range(storage.load_roster(), updated, period)))
        reset_base("schedule", updated)
        return f"{name} → {sch.CHANNEL_LABELS.get(move['new_channel'], move['new_channel'])} on {day_label(day)} {move['from_shift']}", True
    if move.get("to_date"):
        day = date.fromisoformat(move["to_date"])
        if ((updated["datum"] == day) & (updated["dienst"] == move["to_shift"]) & (updated["naam"] == name)).any():
            return f"{name} is already on {day_label(day)} {move['to_shift']}", False
    if move.get("from_date"):
        old = (
            (updated["datum"] == date.fromisoformat(move["from_date"])) & (updated["dienst"] == move["from_shift"])
            & (updated["kanaal"] == move["channel"]) & (updated["naam"] == name)
        )
        updated = updated.drop(updated[old].index[:1])
    if move.get("to_date"):
        day = date.fromisoformat(move["to_date"])
        channel = move.get("channel") or pick_channel(name, day, move["to_shift"])
        problem = sch.assignment_problem(name, day, move["to_shift"], channel, updated, team, shifts,
                                         unavailable, settings["min_rust_uren"])
        if problem:
            return f"Not possible: {problem}", False
        updated = pd.concat([updated, pd.DataFrame([{
            "datum": day, "dienst": move["to_shift"], "kanaal": channel, "naam": name,
        }])], ignore_index=True)
        message = f"{name} → {day_label(day)} {move['to_shift']} ({sch.CHANNEL_LABELS.get(channel, channel)})"
    else:
        message = f"{name} removed from {day_label(date.fromisoformat(move['from_date']))} {move['from_shift']}"
    updated = storage.clean_roster(updated.drop_duplicates())
    persist("schedule", storage.clean_roster(merge_range(storage.load_roster(), updated, period)))
    reset_base("schedule", updated)
    return message, True


with t_matches:
    if match_error is not None:
        st.error(f"Loading matches failed: {match_error}")
    else:
        logo_ids = tuple(sorted({i for g in games.itertuples() for side in matches.logo_candidates(g) for i in side}))
        try:
            urls = load_logo_urls(logo_ids) if logo_ids else {}
        except Exception as exc:  # geen toegang tot Blob Storage: dan initialen
            urls = {}
            st.warning(f"Logos could not be loaded, showing initials instead: {type(exc).__name__}: {exc}")

        # Competitielogo's (LeagueLogoAsset) voor de achtergrond van Europa League- en Nations League-dagen.
        league_assets = tuple(sorted({
            str(g.LeagueLogoAsset).strip() for g in games.itertuples()
            if calendar_view.competition_of(g.competitie) and logos.league_blob(g.LeagueLogoAsset)
        }))
        try:
            league_urls = load_league_logo_urls(league_assets) if league_assets else {}
        except Exception:  # geen competitielogo's: dan alleen de kleur
            league_urls = {}

        if "moved" in st.session_state:
            message, ok = st.session_state.pop("moved")
            st.toast(message, icon="✅" if ok else "⛔")
        clashes = sch.check_conflicts(pd.concat([neighbours(storage.load_roster()), roster], ignore_index=True),
                                      team, shifts, unavailable, settings["min_rust_uren"])
        clashes = clashes[clashes["datum"].notna() & clashes["datum"].isin(period)]
        if not clashes.empty:
            w1, w2 = st.columns([4, 1], vertical_alignment="center")
            w1.warning(f"{len(clashes)} shift(s) break the rules (red in the calendar). "
                       "**Fix** removes only those and fills the gaps again by the rules.")
            if w2.button("🛠 Fix", type="primary", use_container_width=True):
                full = storage.load_roster()
                valid = sch.drop_conflicts(roster, team, shifts, unavailable, settings["min_rust_uren"], neighbours(full))
                fixed = plan(full, keep=valid, variant=0)
                persist("schedule", storage.clean_roster(merge_range(full, fixed, period)))
                reset_base("schedule", storage.clean_roster(fixed))
                st.session_state["moved"] = (f"Fixed: {len(roster) - len(valid)} shift(s) removed, gaps filled again", True)
                st.rerun()
        # Wat nu al niet klopt in het rooster (bijv. te weinig rust) krijgt een rode rand in de kalender.
        flags = {}
        for c in sch.check_conflicts(roster, team, shifts, unavailable, settings["min_rust_uren"]).itertuples():
            if pd.notna(c.datum):
                flags.setdefault((c.datum, c.naam), []).append(c.probleem)
        html = calendar_view.render(
            games, period, dict(zip(calendar["datum"], calendar["dagtype"])), urls,
            now=pd.Timestamp.now(tz=matches.LOCAL_TZ).to_pydatetime(), clubs=clubs, league_urls=league_urls,
            events=events_by_day, roster=roster, flags=flags,
            contracts=dict(zip(active["naam"], active["contracturen"])),
            away={n: {d for r in g.itertuples() for d in period if r.van <= d <= r.tot}
                  for n, g in unavailable.groupby("naam")},
            shift_hours={r.dienst: (lambda w: (w[1] - w[0]).total_seconds() / 3600)(sch.shift_window(start, r.start, r.eind))
                         for r in shifts.itertuples()},
        )
        result = match_calendar(data={"html": html, "people": list(active["naam"]), "channels": sch.CHANNEL_LABELS},
                                key="match_calendar",
                                on_move_change=lambda: None)
        if result.move:
            st.session_state["moved"] = apply_move(result.move)
            st.rerun()
