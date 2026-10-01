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

st.set_page_config(page_title="Schedule · 433", page_icon="⚽", layout="wide")

# Zwarte tekst op de neon-gele labels (bijv. de gekozen clubs), anders is wit op geel onleesbaar.
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

COVERAGE_LABELS = {
    "datum": "Date", "dagtype": "Day type", "dienst": "Shift", "nodig": "Needed", "ingepland": "Scheduled",
    "senior nodig": "Seniors needed", "senior ingepland": "Seniors scheduled", "status": "Status", "mensen": "People",
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
    st.caption(f"Week {start.isocalendar()[1]} to {end.isocalendar()[1]}  \n{start:%d-%m-%Y} – {end:%d-%m-%Y}")
    st.divider()
    st.caption("All changes are saved automatically in the `data/` folder.")

t_matches, t_events, t_team, t_weeks, t_rules, t_absence, t_roster = st.tabs(
    ["⚽ Matches", "⭐ Special events", "👥 Team", "📅 Weeks & staffing", "⚙️ Staffing rules", "🏖️ Time off", "🗓️ Schedule"]
)

# --- Team ----------------------------------------------------------------------------

with t_team:
    st.subheader("Team & seniority")
    st.caption(
        "Senior and Lead count as *senior* in the staffing rules. "
        "Enter fixed days off like `mon, wed`."
    )
    team = st.data_editor(
        base("team", storage.load_team),
        key=editor_key("team"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "naam": st.column_config.TextColumn("Name", required=True),
            "seniority": st.column_config.SelectboxColumn(
                "Seniority", options=sch.SENIORITY_LEVELS, required=True, default="Medior"
            ),
            "max_per_week": st.column_config.NumberColumn(
                "Max shifts / week", min_value=0, max_value=7, step=1, default=5
            ),
            "vrije_dagen": st.column_config.TextColumn("Fixed days off"),
            "actief": st.column_config.CheckboxColumn("Active", default=True),
            "notitie": st.column_config.TextColumn("Note"),
        },
    )
    team = storage.clean_team(team)
    persist("team", team)

    active = team[team["actief"]]
    cols = st.columns(len(sch.SENIORITY_LEVELS) + 1)
    cols[0].metric("Active", len(active))
    for col, level in zip(cols[1:], sch.SENIORITY_LEVELS):
        col.metric(level, int((active["seniority"] == level).sum()))

# --- Bezettingsregels ----------------------------------------------------------------

with t_rules:
    left, right = st.columns([1, 2])
    with left:
        st.subheader("Shifts")
        st.caption("Times as `HH:MM`. An end time before the start time means past midnight.")
        shifts = st.data_editor(
            base("diensten", storage.load_shifts),
            key=editor_key("diensten"),
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_config={
                "dienst": st.column_config.TextColumn("Shift", required=True),
                "start": st.column_config.TextColumn("Start", validate=r"^\d{1,2}:\d{2}$", default="09:00"),
                "eind": st.column_config.TextColumn("End", validate=r"^\d{1,2}:\d{2}$", default="17:00"),
            },
        )
        shifts = storage.clean_shifts(shifts)
        persist("diensten", shifts)
        shift_names = list(shifts["dienst"])

    with right:
        st.subheader("Staffing per day type")
        st.caption(
            "Per day type and shift: how many people are needed and how many of them must be senior. "
            "Add a row with a new name to create a new day type."
        )
        rules = st.data_editor(
            base("regels", storage.load_rules),
            key=editor_key("regels"),
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            height=420,
            column_config={
                "dagtype": st.column_config.TextColumn("Day type", required=True),
                "dienst": st.column_config.SelectboxColumn("Shift", options=shift_names, required=True),
                "aantal": st.column_config.NumberColumn("People", min_value=0, max_value=20, step=1, default=1),
                "min_senior": st.column_config.NumberColumn(
                    "Of which min. senior", min_value=0, max_value=20, step=1, default=0
                ),
            },
        )
        rules = storage.clean_rules(rules)
        persist("regels", rules)

    day_types = list(dict.fromkeys(rules["dagtype"]))

    st.subheader("Overview")
    if not rules.empty:
        matrix = rules.assign(cel=rules["aantal"].astype(str) + " (" + rules["min_senior"].astype(str) + " sr)")
        matrix = matrix.pivot(index="dagtype", columns="dienst", values="cel").reindex(
            index=day_types, columns=[s for s in shift_names if s in set(rules["dienst"])]
        )
        matrix["Total"] = rules.groupby("dagtype")["aantal"].sum().reindex(day_types)
        matrix.index.name = "Day type"
        matrix.columns.name = None
        st.dataframe(matrix.fillna("–"), width="stretch")

    st.subheader("Settings")
    settings = storage.load_settings()
    c1, c2, c3 = st.columns(3)
    new_settings = {
        **settings,  # overige instellingen (zoals de clubs bij Matches) behouden
        "min_rust_uren": c1.number_input(
            "Minimum rest between shifts (hours)", 0, 24, int(settings["min_rust_uren"]),
            help="E.g. no morning shift after an evening shift.",
        ),
        "standaard_doordeweeks": c2.selectbox(
            "Default day type Mon–Fri", day_types,
            index=day_types.index(settings["standaard_doordeweeks"]) if settings["standaard_doordeweeks"] in day_types else 0,
        ),
        "standaard_weekend": c3.selectbox(
            "Default day type Sat–Sun", day_types,
            index=day_types.index(settings["standaard_weekend"]) if settings["standaard_weekend"] in day_types else 0,
        ),
    }
    if new_settings != settings:
        storage.save_settings(new_settings)
    settings = new_settings

# --- Special events ----------------------------------------------------------------

with t_events:
    st.subheader("Special events")
    st.caption("Events show up on their day in the match calendar.")
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

# --- Weken & bezetting ---------------------------------------------------------------

def default_type(d: date) -> str:
    return settings["standaard_weekend"] if d.weekday() >= 5 else settings["standaard_doordeweeks"]


def build_period_calendar(full: pd.DataFrame) -> pd.DataFrame:
    stored = full.set_index("datum")
    rows = []
    for d in period:
        known = d in stored.index
        rows.append(
            {
                "datum": d,
                "week": d.isocalendar()[1],
                "dag": sch.WEEKDAYS[d.weekday()],
                "dagtype": stored.at[d, "dagtype"] if known and stored.at[d, "dagtype"] else default_type(d),
                "notitie": stored.at[d, "notitie"] if known else "",
            }
        )
    return pd.DataFrame(rows)


with t_weeks:
    st.subheader("Weeks & staffing")
    st.caption(
        "Set the type of each day (e.g. Champions League on Tue/Wed). "
        "The required staffing comes from the *Staffing rules* tab."
    )
    full_calendar = storage.load_calendar()
    period_key = (start, int(weeks))
    if st.session_state.get("kalender_period") != period_key:
        st.session_state["kalender_period"] = period_key
        reset_base("kalender", build_period_calendar(full_calendar))

    quick = st.container()
    calendar = st.data_editor(
        st.session_state["kalender_base"],
        key=editor_key("kalender"),
        hide_index=True,
        width="stretch",
        height=min(38 + 35 * len(period), 600),
        disabled=["datum", "week", "dag"],
        column_config={
            "datum": DATE,
            "week": st.column_config.NumberColumn("Week"),
            "dag": st.column_config.TextColumn("Day"),
            "dagtype": st.column_config.SelectboxColumn("Day type", options=day_types, required=True),
            "notitie": st.column_config.TextColumn("Matches / note", width="large"),
        },
    )
    calendar = calendar.assign(dagtype=calendar["dagtype"].fillna(calendar["datum"].map(default_type)))
    calendar["notitie"] = calendar["notitie"].fillna("")
    full_calendar = merge_range(full_calendar, calendar, period)
    persist("kalender", full_calendar)

    with quick.expander("⚡ Quick set: day type for several days at once"):
        q1, q2, q3 = st.columns([2, 2, 1])
        q_days = q1.multiselect("Days", sch.WEEKDAYS, placeholder="e.g. Tue, Wed")
        q_type = q2.selectbox("Day type", day_types, key="quick_type")
        q_weeks = q3.multiselect("Only weeks", sorted(calendar["week"].unique()), placeholder="all")
        if st.button("Apply", disabled=not q_days):
            mask = calendar["dag"].isin(q_days)
            if q_weeks:
                mask &= calendar["week"].isin(q_weeks)
            updated = calendar.copy()
            updated.loc[mask, "dagtype"] = q_type
            persist("kalender", merge_range(full_calendar, updated, period))
            reset_base("kalender", updated)
            st.rerun()

    # Benodigde bezetting per week vs. beschikbare capaciteit.
    need = calendar.merge(rules, on="dagtype", how="left").fillna({"aantal": 0, "min_senior": 0})
    per_week = need.groupby("week").agg(nodig=("aantal", "sum"), senior_nodig=("min_senior", "sum"))
    capacity = {}
    for week, days in calendar.groupby("week")["datum"]:
        cap = 0
        for p in active.itertuples():
            free = sch.parse_free_days(p.vrije_dagen)
            available = sum(
                d.weekday() not in free
                and not ((absences["naam"] == p.naam) & (absences["van"] <= d) & (absences["tot"] >= d)).any()
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
            "senior_nodig": "Of which senior",
            "capaciteit": st.column_config.NumberColumn("Team capacity", help="Sum of max shifts, taking time off into account"),
            "marge": "Margin",
        },
    )

# --- Wedstrijden ---------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner="Loading matches…")
def load_matches(first: date, last: date, clubs: tuple[str, ...]) -> pd.DataFrame:
    return matches.fetch_matches(first, last, clubs)


@st.cache_data(ttl=3600, show_spinner="Loading logos…")
def load_logo_urls(ids: tuple[int, ...]) -> dict[int, str]:
    return logos.logo_urls(ids)  # links zijn langer geldig (logos.LINK_HOURS) dan de cache


@st.cache_data(ttl=3600, show_spinner=False)
def load_league_logo_urls(assets: tuple[str, ...]) -> dict[str, str]:
    return logos.league_logo_urls(assets)


with t_matches:
    clubs = matches.DEFAULT_CLUBS
    found = None
    if not clubs:
        st.info("Pick at least one club.")
    else:
        try:
            found = load_matches(start, end, tuple(clubs))
        except Exception as exc:  # database niet bereikbaar, .env niet ingevuld, ...
            st.error(f"Loading matches failed: {exc}")

    if found is not None:
        names = matches.team_names(found, clubs)
        teams = matches.default_teams(names, clubs)
        games = matches.for_teams(found, teams)

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

        st.html(
            calendar_view.render(
                games, period, dict(zip(calendar["datum"], calendar["dagtype"])), urls,
                now=pd.Timestamp.now(tz=matches.LOCAL_TZ).to_pydatetime(), clubs=clubs, league_urls=league_urls,
                events=events.groupby("datum")["titel"].apply(list).to_dict(),
            )
        )

# --- Rooster -------------------------------------------------------------------------

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
    sch.STATUS_NO_SENIOR: "background-color: #fde6c4",
    sch.STATUS_OVER: "background-color: #dfe7fb",
}
SHORTAGES = [sch.STATUS_SHORT, sch.STATUS_NO_SENIOR]


def styled_status(df: pd.DataFrame):
    return df.rename(columns=COVERAGE_LABELS).style.map(lambda v: STATUS_COLORS.get(v, ""), subset=["Status"])


with t_roster:
    st.subheader("Schedule")
    full_roster = storage.load_roster()

    g1, g2, g3 = st.columns([1, 1, 3], vertical_alignment="bottom")
    seed = g1.number_input("Variant", min_value=0, value=0, step=1, help="Different number = different proposal")
    if g2.button("✨ Generate proposal", type="primary"):
        proposal = sch.generate_schedule(
            team, shifts, rules, calendar, absences, settings["min_rust_uren"], int(seed)
        )
        persist("rooster", storage.clean_roster(merge_range(full_roster, proposal, period)))
        reset_base("rooster", storage.clean_roster(proposal))
        st.session_state["rooster_period"] = period_key
        st.rerun()
    g3.caption(
        "The proposal fills the required senior slots first, then spreads the shifts fairly, "
        "taking time off, days off, max per week and rest time into account. "
        "A new proposal overwrites the schedule for this period."
    )

    if st.session_state.get("rooster_period") != period_key:
        st.session_state["rooster_period"] = period_key
        reset_base("rooster", full_roster[full_roster["datum"].isin(period)].reset_index(drop=True))

    with st.expander("✏️ Edit manually", expanded=False):
        st.caption("Add or remove rows; the checks below update immediately.")
        roster = st.data_editor(
            st.session_state["rooster_base"],
            key=editor_key("rooster"),
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            height=400,
            column_config={
                "datum": st.column_config.DateColumn(
                    "Date", format="DD-MM-YYYY", min_value=start, max_value=end, required=True
                ),
                "dienst": st.column_config.SelectboxColumn("Shift", options=shift_names, required=True),
                "naam": st.column_config.SelectboxColumn("Name", options=list(team["naam"]), required=True),
            },
        )
    roster = storage.clean_roster(roster)
    persist("rooster", storage.clean_roster(merge_range(full_roster, roster, period)))

    if roster.empty:
        st.info("No schedule for this period yet. Click **Generate proposal**.")
        st.stop()

    coverage = sch.check_coverage(roster, team, shifts, rules, calendar)
    conflicts = sch.check_conflicts(roster, team, shifts, absences, settings["min_rust_uren"])
    problems = coverage[coverage["status"] != sch.STATUS_OK]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Shifts scheduled", len(roster))
    m2.metric("Slots OK", f"{(coverage['status'] == sch.STATUS_OK).sum()} / {len(coverage)}")
    m3.metric("Shortages", int((coverage["status"].isin(SHORTAGES)).sum()))
    m4.metric("Rule conflicts", len(conflicts))

    level = dict(zip(team["naam"], team["seniority"]))

    def names_cell(group: pd.Series) -> str:
        return ", ".join(f"{n} ★" if sch.is_senior(level.get(n, "")) else n for n in sorted(group))

    per_day = (
        roster.groupby(["datum", "dienst"])["naam"].apply(names_cell).unstack("dienst")
        .reindex(columns=shift_names)
    )
    per_day = (
        calendar.set_index("datum")[["week", "dag", "dagtype", "notitie"]]
        .join(per_day)
        .fillna("")
    )
    short = coverage[coverage["status"].isin(SHORTAGES)]
    for row in short.itertuples():
        missing = row.nodig - row.ingepland
        note = f"⚠ {missing} short" if missing > 0 else "⚠ senior short"
        current = per_day.at[row.datum, row.dienst]
        per_day.at[row.datum, row.dienst] = f"{current}  {note}".strip()
    per_day.index = [day_label(d) for d in per_day.index]
    per_day = per_day.rename(columns={"week": "Week", "dag": "Day", "dagtype": "Day type", "notitie": "Note"})

    per_person = roster.assign(dag=roster["datum"].map(day_label)).pivot_table(
        index="naam", columns="dag", values="dienst", aggfunc=lambda x: " + ".join(x)
    )
    per_person = per_person.reindex(
        index=[n for n in team["naam"] if n in per_person.index] + [n for n in per_person.index if n not in set(team["naam"])],
        columns=[day_label(d) for d in period],
    ).fillna("")
    per_person.index.name = "Name"
    per_person.columns.name = None

    summary = pd.crosstab(roster["naam"], roster["dienst"]).reindex(columns=shift_names, fill_value=0)
    summary.insert(0, "Total", summary.sum(axis=1))
    summary.insert(1, "Weekend", roster[roster["datum"].map(lambda d: d.weekday() >= 5)].groupby("naam").size())
    summary = team.set_index("naam")[["seniority", "max_per_week"]].join(summary, how="left").fillna(0)
    summary["Avg. per week"] = (summary["Total"] / int(weeks)).round(1)
    summary = summary.rename(columns={"seniority": "Seniority", "max_per_week": "Max / week"})
    summary.index.name = "Name"
    for col in summary.columns.drop(["Seniority", "Avg. per week"]):
        summary[col] = summary[col].astype(int)

    v_day, v_person, v_check, v_split = st.tabs(["Per day", "Per person", "Staffing check", "Distribution"])
    with v_day:
        st.caption("★ = senior/lead")
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
            st.dataframe(
                conflicts.rename(columns=CONFLICT_LABELS), hide_index=True, width="stretch", column_config={"Date": DATE}
            )
        with st.expander("All shifts"):
            st.dataframe(styled_status(coverage), hide_index=True, width="stretch", column_config={"Date": DATE})
    with v_split:
        st.caption("How the shifts are spread across the team.")
        st.dataframe(summary, width="stretch")

    st.download_button(
        "⬇️ Download as Excel",
        data=to_excel(
            {
                "Schedule per day": per_day,
                "Per person": per_person,
                "Distribution": summary,
                "Staffing": coverage.rename(columns=COVERAGE_LABELS),
                "Conflicts": conflicts.rename(columns=CONFLICT_LABELS),
            }
        ),
        file_name=f"schedule_week{start.isocalendar()[1]}-{end.isocalendar()[1]}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
