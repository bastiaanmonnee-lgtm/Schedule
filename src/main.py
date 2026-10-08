"""Schedule tool for the social media team (football).

Start met:  streamlit run src/main.py   (of: docker compose up)
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import auth
import calendar_view
import logos
import matches
import scheduler as sch
import storage
from clubs import DUTCH_CLUBS

st.set_page_config(page_title="Schedule · 433", page_icon="⚽", layout="wide", initial_sidebar_state="collapsed")

# Zwarte tekst op de neon-gele labels (bijv. de gekozen kanalen), anders is wit op geel onleesbaar.
st.markdown(
    """<style>
    [data-baseweb="tag"], [data-baseweb="tag"] * { color: #000 !important; }
    [data-baseweb="tag"] svg { fill: #000 !important; }
    /* Meldingen (bijv. "Not possible: ...") in het midden van het scherm i.p.v. rechtsonder */
    [data-testid="stToastContainer"] { top: 50% !important; bottom: auto !important; left: 50% !important;
                                       right: auto !important; transform: translate(-50%, -50%); align-items: center; }
    [data-testid="stToast"] { min-width: 460px; padding: 22px 26px !important; font-size: 1.15rem; }
    [data-testid="stToast"] p { font-size: 1.15rem !important; font-weight: 600; line-height: 1.4; }
    [data-testid="stToast"] [data-testid="stToastDynamicIcon"], [data-testid="stToast"] span[role="img"] { font-size: 1.6rem; }
    </style>""",
    unsafe_allow_html=True,
)

# --- Inloggen ------------------------------------------------------------------------
# Zelfde opzet als de expense-claim-generator (streamlit-authenticator, zie auth.py).

authenticator, user = auth.require_login()
with st.sidebar:
    st.caption(f"Logged in as **{user.name}** ({user.role})")
    authenticator.logout(location="sidebar")

if not user.is_admin:
    # Medewerkers: alleen hun eigen diensten (de indienpagina voor wensen komt hier later bij).
    st.title(f"Hi {user.employee} 👋")
    mine = storage.load_roster()
    mine = mine[(mine["naam"] == user.employee) & (mine["datum"] >= date.today())]
    if mine.empty:
        st.info("You have no upcoming shifts yet.")
    else:
        times = {r.dienst: f"{r.start}–{r.eind}" for r in storage.load_shifts().itertuples()}
        st.dataframe(
            pd.DataFrame({
                "Date": mine["datum"].map(lambda d: f"{sch.WEEKDAYS[d.weekday()]} {d:%d-%m-%Y}"),
                "Shift": [f"{d} ({times.get(d, '')})" for d in mine["dienst"]],
                "Channel": mine["kanaal"].map(sch.CHANNEL_LABELS),
            }),
            hide_index=True, width="stretch",
        )
    st.stop()


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


# --- periode: 12 weken vanaf deze week -------------------------------------------------
# Het rooster maak je per maand zelf met een knop (alleen de dagen van die maand die in beeld zijn).

WEEKS_SHOWN = 12
today = date.today()
start = monday(today) + timedelta(days=7)  # vanaf volgende week (de lopende week staat er al)
period = [start + timedelta(days=i) for i in range(WEEKS_SHOWN * 7)]
end = period[-1]
weeks = WEEKS_SHOWN
period_key = (start, weeks)

# Rooster maken: een reeks hele weken (van week … t/m week …, standaard 5 weken vanaf deze week; vanaf vandaag; de dagen van deze week die al voorbij zijn
# blijven staan en tellen mee voor uren en rusttijd). Zo valt een week nooit half buiten het rooster.
NEXT_WEEKS = "next weeks"
week_starts = period[::7]
week_name = lambda d: f"Week {d.isocalendar()[1]} ({d.day} {d:%b})"  # noqa: E731
b0, b0b, b1, mc3 = st.columns([1, 1, 2, 3], vertical_alignment="bottom")
first_week = b0.selectbox("From week", week_starts, index=0, format_func=week_name)
last_options = [w for w in week_starts if w >= first_week]
last_week = b0b.selectbox("Until week (incl.)", last_options, index=min(4, len(last_options) - 1), format_func=week_name)
# Vanaf vandaag: dagen die al voorbij zijn blijven staan.
month_days = [d for d in period if first_week <= d <= last_week + timedelta(days=6) and d >= today]
from_week, to_week = first_week.isocalendar()[1], last_week.isocalendar()[1]
month_label = f"week {from_week}–{to_week}" if from_week != to_week else f"week {from_week}"
if b1.button(f"✨ Make schedule: {month_label}", key="make_weeks", type="primary", use_container_width=True,
             help="Plans every day in these weeks from today on; days that are already past stay as they are."):
    st.session_state["make_month"] = NEXT_WEEKS
month = NEXT_WEEKS  # mc3: hier komt zo nodig de vraag of een bestaand rooster vervangen mag worden

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

ucl_days = set()
if games is not None:
    ucl_days = {g.datum for g in games.itertuples()
                if (calendar_view.competition_of(g.competitie) or ("",))[0] == "ucl"}

t_matches, t_events, t_team, t_zero, t_weeks, t_rules, t_absence = st.tabs(
    ["⚽ Matches", "⭐ Special events", "👥 Employees", "⏱️ Zero hours", "📅 Weeks & staffing", "⚙️ Staffing rules",
     "🏖️ Time off"]
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
        kanalen=lambda df: df["kanalen"].map(lambda v: [sch.CHANNEL_LABELS[c] for c in sch.channels_of(v)]),
        avond_vanaf=lambda df: pd.to_datetime(df["avond_vanaf"], errors="coerce").dt.date,  # datumkiezer in de tabel
    )
    team = st.data_editor(
        team_view,
        key=editor_key("employees"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_order=["naam", "kanalen", "contracturen", "nuluren", "auto", "geen_avond", "avond_vanaf", "avond_samen", "max_per_week", "vaste_dienst", "vaste_dagen",
                      "ucl", "vrije_dagen", "seniority"],
        column_config={
            "naam": st.column_config.TextColumn("Name", required=True),
            "kanalen": st.column_config.MultiselectColumn("Channels", options=CHANNEL_OPTIONS),
            "contracturen": st.column_config.NumberColumn(
                "Contract hours / week", min_value=0, max_value=60, step=1, default=0,
                help="Shown next to each week in the match calendar: red = fewer hours scheduled, orange = more.",
            ),
            "nuluren": st.column_config.CheckboxColumn(
                "Zero hours", default=False,
                help="Only scheduled on the days you enter in the Zero hours tab (and then always); never topped up.",
            ),
            "auto": st.column_config.CheckboxColumn(
                "Auto schedule", default=True, help="Off: the planner skips this person; you can still add them by hand.",
            ),
            "geen_avond": st.column_config.CheckboxColumn(
                "No evenings", default=False, help="Never scheduled in the evening shift (16:00–00:00).",
            ),
            "avond_vanaf": st.column_config.DateColumn(
                "Evenings from", format="DD-MM-YYYY",
                help="No evening shifts before this date (e.g. new colleagues). Empty = always allowed.",
            ),
            "avond_samen": st.column_config.CheckboxColumn(
                "Evenings only together", default=False,
                help="In the evening someone else must be on the same channel (e.g. on Main with a second person).",
            ),
            "max_per_week": st.column_config.NumberColumn("Max shifts / week", min_value=0, max_value=7, step=1, default=5),
            "vaste_dienst": st.column_config.SelectboxColumn("Always shift", options=["", sch.DAY, sch.EVENING]),
            "vaste_dagen": st.column_config.TextColumn("Always on"),
            "ucl": st.column_config.CheckboxColumn("UCL nights", default=False),
            "vrije_dagen": st.column_config.TextColumn("Fixed days off"),
            "seniority": st.column_config.SelectboxColumn(
                "Role", options=sch.SENIORITY_LEVELS, default="Medior",
                format_func=lambda v: "Intern (stagiair)" if v == sch.INTERN else v,
                help="Intern (stagiair): always Mon–Fri day shifts, never weekends, never alone on their channel.",
            ),
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
        h1, h2 = st.columns([3, 1], vertical_alignment="bottom")
        h1.subheader("Staffing per day type")
        if h2.button("↺ Reset to recommended", use_container_width=True,
                     help="Back to the standard staffing based on the old schedule (1 per channel per shift)."):
            persist("staffing", storage.DEFAULT_RULES.copy())
            reset_base("staffing", storage.DEFAULT_RULES.copy())
            st.session_state["moved"] = ("Staffing per day type reset to the recommended values.", True)
            st.rerun()
        st.caption(
            "People needed per channel. Day types are set automatically: *Champions League* on UCL nights, "
            "*Weekend* on Sat/Sun, otherwise *Regular day*. Extra people for matches and events: see the table below."
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

    st.subheader("Person rules")
    st.caption(
        "Extra rules per person; the planner, the checks and drag & drop all follow them. "
        "Value per rule: " + " · ".join(f"**{rule}**: {how}" for rule, how in sch.RULE_VALUES.items())
    )
    person_rules = st.data_editor(
        base("person_rules", storage.load_person_rules),
        key=editor_key("person_rules"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "naam": st.column_config.SelectboxColumn("Name", options=list(team["naam"]), required=True),
            "regel": st.column_config.SelectboxColumn("Rule", options=sch.RULE_TYPES, required=True),
            "waarde": st.column_config.TextColumn("Value", required=True, help="Channel, shift or person, depending on the rule"),
        },
    )
    person_rules = storage.clean_person_rules(person_rules)
    persist("person_rules", person_rules)

    st.subheader("Extra staffing for matches & events")
    st.caption(
        "Extra people per channel in the shift of the kick-off (special events: evening; night matches: none). "
        "Not on Champions League evenings: those have a fixed staffing in the table above. "
        f"**{sch.TRIGGER_TEAM}**: value = a team, e.g. Netherlands or Ajax. "
        f"**{sch.TRIGGER_DUTCH}**: value = a competition, e.g. Champions League; a Dutch club (clubs.py) plays. "
        f"**{sch.TRIGGER_COMPETITION}**: value = (part of) the competition, e.g. Nations League. "
        f"**{sch.TRIGGER_BIG}**: two clubs from the top {sch.TOP_CLUBS} of clubs.py. "
        f"**{sch.TRIGGER_EVENT}**: every special event."
    )
    match_staffing = st.data_editor(
        base("match_staffing", storage.load_match_staffing),
        key=editor_key("match_staffing"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "aanleiding": st.column_config.SelectboxColumn("When", options=sch.TRIGGERS, required=True),
            "waarde": st.column_config.TextColumn("Value", help="Team or competition; empty for Big match / Special event"),
            **{c: st.column_config.NumberColumn(f"+ {sch.CHANNEL_LABELS[c]}", min_value=0, max_value=10, step=1, default=0)
               for c in sch.CHANNELS},
        },
    )
    match_staffing = storage.clean_match_staffing(match_staffing)
    persist("match_staffing", match_staffing)

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
    st.caption("People who are absent in this period are not scheduled. Pick a name and a date (or a range), then Add.")
    with st.form("add_time_off", clear_on_submit=True, border=True):
        f1, f2, f2b, f3 = st.columns([2, 3, 2, 1], vertical_alignment="bottom")
        off_name = f1.selectbox("Name", list(team["naam"]), index=None, placeholder="Choose a person")
        off_part = f2b.selectbox("Part", ["Whole day", "Day off", "Evening off"],
                                 help="Day off = no day shift (an evening is fine); Evening off = no evening shift.")
        off_days = f2.date_input("From – until", value=[], format="DD-MM-YYYY",
                                 help="Click a start date and an end date; click one date twice for a single day.")
        if f3.form_submit_button("➕ Add", type="primary", use_container_width=True):
            days = list(off_days) if isinstance(off_days, (list, tuple)) else [off_days]
            if off_name and days:
                added = pd.DataFrame([{"naam": off_name, "van": days[0], "tot": days[-1],
                                       "deel": off_part, "reden": ""}])
                current = storage.clean_absences(pd.concat([base("afwezigheid", storage.load_absences), added], ignore_index=True))
                persist("afwezigheid", current)
                reset_base("afwezigheid", current)
                st.rerun()
            else:
                st.warning("Choose a person and a date first.")
    st.caption("Change or delete below (select a row and press Delete).")
    absences = st.data_editor(
        base("afwezigheid", storage.load_absences).assign(  # leesbaar in de tabel; opgeslagen als "", Day, Evening
            deel=lambda df: df["deel"].map({"": "Whole day", "Day": "Day off", "Evening": "Evening off"}).fillna("Whole day")
        ),
        key=editor_key("afwezigheid"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_order=["naam", "van", "tot", "deel"],
        column_config={
            "naam": st.column_config.SelectboxColumn("Name", options=list(team["naam"]), required=True),
            "van": st.column_config.DateColumn("From", format="DD-MM-YYYY", required=True),
            "tot": st.column_config.DateColumn("Until (incl.)", format="DD-MM-YYYY"),
            "deel": st.column_config.SelectboxColumn("Part", options=["Whole day", "Day off", "Evening off"],
                                                     default="Whole day", required=True),
        },
    )
    absences = storage.clean_absences(absences)
    persist("afwezigheid", absences)

# --- Nuluren ---------------------------------------------------------------------------

zero_people = list(team.loc[team["nuluren"] & team["actief"], "naam"])
with t_zero:
    st.subheader("Zero hours")
    st.caption(
        "People with **Zero hours** ticked on the Employees page are only scheduled on the days below, and then always "
        "(in the shift you choose, or wherever needed with *Any*). They are never topped up to contract hours."
    )
    if not zero_people:
        st.info("Nobody has **Zero hours** ticked yet: do that on the Employees page first.")
    with st.form("add_zero_hours", clear_on_submit=True, border=True):
        z1, z2, z3, z4 = st.columns([2, 3, 2, 1], vertical_alignment="bottom")
        z_name = z1.selectbox("Name", zero_people, index=None, placeholder="Choose a person")
        z_day = z2.date_input("Day", value=None, format="DD-MM-YYYY")
        z_shift = z3.selectbox("Shift", ["Any", sch.DAY, sch.EVENING])
        if z4.form_submit_button("➕ Add", type="primary", use_container_width=True):
            if z_name and z_day:
                added = pd.DataFrame([{"naam": z_name, "datum": z_day, "dienst": z_shift}])
                current = storage.clean_zero_hours(pd.concat([base("zero_hours", storage.load_zero_hours), added]))
                persist("zero_hours", current)
                reset_base("zero_hours", current)
                st.rerun()
            else:
                st.warning("Choose a person and a day first.")
    st.caption("Change a name, day or shift by clicking the cell. Tick **Delete** to remove a line.")
    edited_zero = st.data_editor(
        base("zero_hours", storage.load_zero_hours).assign(delete=False),
        key=editor_key("zero_hours"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "naam": st.column_config.SelectboxColumn("Name", options=list(team["naam"]), required=True),
            "datum": st.column_config.DateColumn("Day", format="DD-MM-YYYY", required=True),
            "dienst": st.column_config.SelectboxColumn("Shift", options=["Any", sch.DAY, sch.EVENING], default="Any"),
            "delete": st.column_config.CheckboxColumn("Delete", default=False, help="Tick to remove this line"),
        },
    )
    remove = edited_zero["delete"].fillna(False).astype(bool)
    zero_hours = storage.clean_zero_hours(edited_zero[~remove].drop(columns="delete"))
    before = storage.clean_zero_hours(base("zero_hours", storage.load_zero_hours))
    persist("zero_hours", zero_hours)
    if not zero_hours.reset_index(drop=True).equals(before.reset_index(drop=True)):
        # Iets aangepast of weggehaald: de tabel voortaan opbouwen vanuit wat nu is opgeslagen.
        reset_base("zero_hours", zero_hours)

# Regels voor de planner: de person rules plus de nuluren-afspraken (als interne regel per persoon).
# Avonden pas vanaf een datum / alleen samen (Employees) als regels voor de planner.
evening_rules = pd.DataFrame(
    [{"naam": r.naam, "regel": sch.RULE_EVENING_FROM, "waarde": r.avond_vanaf} for r in team.itertuples() if r.avond_vanaf]
    + [{"naam": r.naam, "regel": sch.RULE_EVENING_TOGETHER, "waarde": "-"} for r in team.itertuples() if r.avond_samen],
    columns=["naam", "regel", "waarde"],
)

# Stagiairs (Role = Intern): altijd ma t/m vr overdag, nooit alleen op hun kanaal. Zelfde regels voor elke stagiair.
intern_rules = pd.DataFrame(
    [{"naam": r.naam, "regel": sch.RULE_ALWAYS, "waarde": "Day: mon, tue, wed, thu, fri"}
     for r in team.itertuples() if r.seniority == sch.INTERN]
    + [{"naam": r.naam, "regel": sch.RULE_NOT_ALONE, "waarde": k}
       for r in team.itertuples() if r.seniority == sch.INTERN for k in sch.channels_of(r.kanalen)],
    columns=["naam", "regel", "waarde"],
)

planning_rules = pd.concat([person_rules, evening_rules, intern_rules, pd.DataFrame([
    {"naam": n, "regel": sch.RULE_ZERO,
     "waarde": ";".join(f"{r.datum.isoformat()}:{r.dienst}" for r in zero_hours[zero_hours["naam"] == n].itertuples())}
    for n in zero_people
], columns=["naam", "regel", "waarde"])], ignore_index=True)

# Voor de planning: vrij/afwezig plus de maanden bij een ander team (WomenFC).
unavailable = pd.concat([absences, storage.other_team_as_absences(other_team)], ignore_index=True)

# --- Weken & bezetting ---------------------------------------------------------------

def match_extras() -> dict[date, list[tuple]]:
    """Extra bezetting per dag uit de tabel 'Extra staffing for matches & events':
    {datum: [(dienst, kanaal, aantal, reden), ...]}. Per regel telt een dienst één keer mee."""
    extras: dict[date, list[tuple]] = {}
    seen = set()

    def add(day, dienst, rule, reason):
        if day not in period or dienst not in (sch.DAY, sch.EVENING) or (day, dienst, rule.Index) in seen:
            return  # nachtwedstrijden: de nachtdienst is handmatig
        if day in ucl_days and dienst == sch.EVENING:
            return  # Champions League-avond: vaste bezetting (Staffing per day type), geen extra's erbovenop
        seen.add((day, dienst, rule.Index))
        for kanaal in sch.CHANNELS:
            if int(getattr(rule, kanaal)):
                extras.setdefault(day, []).append((dienst, kanaal, int(getattr(rule, kanaal)), reason))

    for rule in match_staffing.itertuples():
        if rule.aanleiding == sch.TRIGGER_EVENT:
            for day, titles in events_by_day.items():
                add(day, sch.EVENING, rule, " / ".join(titles))
        if games is None:
            continue
        if rule.aanleiding == sch.TRIGGER_BIG:
            for g in matches.big_matches(games, clubs, sch.TOP_CLUBS):
                add(g.datum, calendar_view.game_shift(g.tijd), rule, f"{g.HomeTeam} – {g.AwayTeam}")
        elif rule.aanleiding == sch.TRIGGER_TEAM and rule.waarde:
            for g in games.itertuples():
                if matches.is_club(g.HomeTeam, rule.waarde) or matches.is_club(g.AwayTeam, rule.waarde):
                    add(g.datum, calendar_view.game_shift(g.tijd), rule, f"{rule.waarde} plays")
        elif rule.aanleiding == sch.TRIGGER_DUTCH and rule.waarde:
            for g in games.itertuples():
                dutch = next((c for c in DUTCH_CLUBS for t in (g.HomeTeam, g.AwayTeam) if matches.is_club(t, c)), None)
                if dutch and rule.waarde.lower() in str(g.competitie or "").lower():
                    add(g.datum, calendar_view.game_shift(g.tijd), rule, f"{dutch} in {rule.waarde}")
        elif rule.aanleiding == sch.TRIGGER_COMPETITION and rule.waarde:
            for g in games.itertuples():
                if rule.waarde.lower() in str(g.competitie or "").lower():
                    add(g.datum, calendar_view.game_shift(g.tijd), rule, rule.waarde)
    return extras


def build_period_calendar(days: pd.DataFrame) -> pd.DataFrame:
    stored = days.set_index("datum")
    rows = []
    for d in period:
        manual = stored.at[d, "handmatig"] if d in stored.index else ""
        reason = " / ".join(dict.fromkeys(f"{x[3]} (+{sch.CHANNEL_LABELS[x[1]]} {x[0].lower()})"
                                          for x in extras_by_day.get(d, [])))
        rows.append({
            "datum": d,
            "week": d.isocalendar()[1],
            "dag": sch.WEEKDAYS[d.weekday()],
            "auto": sch.auto_day_type(d, ucl_days),
            "handmatig": manual if manual in day_types else "",
            "extra": reason,
            "notitie": stored.at[d, "notitie"] if d in stored.index else "",
        })
    return pd.DataFrame(rows)


extras_by_day = match_extras()
games_per_shift: dict[date, dict[str, int]] = {}
for g in (games.itertuples() if games is not None else []):
    shift = calendar_view.game_shift(g.tijd)
    games_per_shift.setdefault(g.datum, {})[shift] = games_per_shift.get(g.datum, {}).get(shift, 0) + 1

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
    calendar = edited.assign(
        dagtype=edited["handmatig"].where(edited["handmatig"] != "", edited["auto"]),
        extras=edited["datum"].map(lambda d: extras_by_day.get(d, [])),
        # Aantal wedstrijden per dienst: extra mensen (aanvullen tot contract) gaan waar het druk is.
        wedstrijden=edited["datum"].map(lambda d: games_per_shift.get(d, {})),
    )

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
                and not ((unavailable["naam"] == p.naam) & (unavailable["van"] <= d) & (unavailable["tot"] >= d)
                         & (unavailable["deel"] == "")).any()
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
    """Diensten rond de maand (de rest van de gedeelde weken en de dag ervoor/erna):
    die tellen mee voor de rusttijd en het aantal diensten per week."""
    around = (set(period) | {start - timedelta(days=1), end + timedelta(days=1)}) - set(month_days)
    return full[full["datum"].isin(around)]


def make_month_schedule(full: pd.DataFrame, variant: int) -> pd.DataFrame:
    """Rooster voor de dagen van de gekozen maand dat nergens tegen een regel ingaat; slaat het op."""
    context = neighbours(full)
    proposal = sch.generate_schedule(team, shifts, rules, calendar[calendar["datum"].isin(month_days)], unavailable,
                                     settings["min_rust_uren"], variant, existing=context, person_rules=planning_rules)
    # Vangnet: wat toch zou botsen (bijv. met een dienst vlak voor de maand) gaat eruit.
    proposal = sch.drop_conflicts(proposal, team, shifts, unavailable, settings["min_rust_uren"], context, planning_rules)
    updated = storage.clean_roster(merge_range(full, proposal, month_days))
    persist("schedule", updated)
    return updated


# Het rooster: geen eigen tabblad, je werkt in de matchkalender (slepen, + en ×).
full_roster = storage.load_roster()

# Knop bovenaan: rooster maken voor de maand (met bevestiging als er al een rooster staat).
if st.session_state.get("make_month") == month:
    has_schedule = full_roster["datum"].isin(month_days).any()
    if has_schedule and not st.session_state.get("make_month_confirmed"):
        with mc3:
            st.warning(f"There is already a schedule in {month_label}. Replace it?")
            y, n = st.columns(2)
            if y.button("Yes, replace", type="primary", use_container_width=True):
                st.session_state["make_month_confirmed"] = True
                st.rerun()
            if n.button("Cancel", use_container_width=True):
                st.session_state.pop("make_month")
                st.rerun()
    else:
        updated = make_month_schedule(full_roster, 0)
        reset_base("schedule", updated[updated["datum"].isin(period)].reset_index(drop=True))
        st.session_state["schedule_period"] = period_key
        st.session_state.pop("make_month")
        st.session_state.pop("make_month_confirmed", None)
        st.session_state["moved"] = (f"Schedule for {month_label} made.", True)
        st.rerun()

if st.session_state.get("schedule_period") != period_key:
    st.session_state["schedule_period"] = period_key
    reset_base("schedule", full_roster[full_roster["datum"].isin(period)].reset_index(drop=True))
roster = storage.clean_roster(st.session_state["schedule_base"])

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

  // Iemand toevoegen: via + (kanaal naar keuze) of door op een rode "open"-plek te klikken (kanaal staat vast).
  // Rode "open"-plek: kies uit de mensen die die dienst volgens alle regels echt kunnen doen.
  const openHolePicker = (hole) => {
      const row = hole.closest("[data-drop-date]");
      const key = `${row.dataset.dropDate}|${row.dataset.dropShift}|${hole.dataset.openChannel}`;
      const names = (data.open_candidates || {})[key] || [];
      const select = document.createElement("select");
      select.className = "mc-picker";
      select.innerHTML = names.length
        ? '<option value="">Choose who…</option>' + names.map((n) => `<option value="${n}">${n}</option>`).join("")
        : '<option value="">Nobody can work this by the rules</option>';
      select.addEventListener("change", () => {
        if (select.value) setTriggerValue("move", { name: select.value, from_date: null, from_shift: null,
          channel: hole.dataset.openChannel, to_date: row.dataset.dropDate, to_shift: row.dataset.dropShift });
      });
      select.addEventListener("keydown", (e) => { if (e.key === "Escape") select.replaceWith(hole); });
      select.addEventListener("blur", () => setTimeout(() => { if (select.isConnected) select.replaceWith(hole); }, 200));
      hole.replaceWith(select);
      select.focus();
  };

  const openPicker = (plus, presetChannel) => {
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
      if (presetChannel) channel.value = presetChannel;
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
      picker.addEventListener("input", () => { if (presetChannel && data.people.includes(picker.value)) add(); });
      // Klik buiten het vakje: sluiten (niet als je van naam naar kanaal gaat).
      box.addEventListener("focusout", () => setTimeout(() => { if (!box.matches(":focus-within")) close(); }, 200));
      plus.replaceWith(box);
      picker.focus();
  };
  root.querySelectorAll(".mc-add").forEach((plus) => plus.addEventListener("click", () => openPicker(plus, null)));
  root.querySelectorAll(".mc-open").forEach((hole) => hole.addEventListener("click", (e) => {
    e.stopPropagation();
    openHolePicker(hole);
  }));

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


# Regels die de planner volgt, maar die je met de hand wel mag doorbreken (je krijgt alleen een waarschuwing).
MANUAL_ALLOWED = ("Two weekends in a row",)


def manual_ok(problem: str) -> bool:
    return problem.startswith(MANUAL_ALLOWED)


def new_rule_breaks(before: pd.DataFrame, after: pd.DataFrame, include_allowed: bool = False) -> list[str]:
    """Persoonsregels die door een wijziging nieuw gebroken worden (zonder MANUAL_ALLOWED, tenzij gevraagd)."""
    def breaks(df):
        found = sch.check_conflicts(df, team, shifts, unavailable, settings["min_rust_uren"], person_rules=planning_rules)
        return {f"{r.probleem} ({r.datum})" for r in found.itertuples() if pd.notna(r.datum)}
    new = sorted(breaks(after) - breaks(before))
    return new if include_allowed else [p for p in new if not manual_ok(p)]


def open_candidates(missing: dict) -> dict[str, list[str]]:
    """Per open plek ("datum|dienst|kanaal") wie die dienst volgens alle regels echt kan doen."""
    def adds_problem(name, day, dienst, kanaal) -> bool:
        # Snel: alleen de diensten van die persoon en van die dag bekijken (niet het hele rooster).
        part = roster[(roster["naam"] == name) | (roster["datum"] == day)]
        new_row = pd.DataFrame([{"datum": day, "dienst": dienst, "kanaal": kanaal, "naam": name}])
        return bool(new_rule_breaks(part, pd.concat([part, new_row], ignore_index=True)))

    result = {}
    for (day, dienst), channels in missing.items():
        here = roster[(roster["datum"] == day) & (roster["dienst"] == dienst)]
        crew = list(zip(here["naam"], here["kanaal"]))
        for kanaal in channels:
            result[f"{day.isoformat()}|{dienst}|{kanaal}"] = [
                n for n in active["naam"]
                if sch.assignment_problem(n, day, dienst, kanaal, roster, team, shifts, unavailable,
                                          settings["min_rust_uren"]) is None
                and not sch.rule_breaks(n, day, dienst, kanaal, crew, planning_rules)
                and not adds_problem(n, day, dienst, kanaal)
            ]
    return result


def explain_hours(name: str, days: list[date]) -> str:
    """Waarom heeft `name` op de vrije dagen van deze week geen dienst? (tooltip in de urenkolom)"""
    channels = sch.channels_of(team.set_index("naam")["kanalen"].get(name, "")) or sch.CHANNELS
    worked = set(roster.loc[roster["naam"] == name, "datum"])
    lines = []
    for d in days:
        if d in worked or d < today:
            continue
        reasons = []
        for shift in (sch.DAY, sch.EVENING):
            for k in channels:
                problem = sch.assignment_problem(name, d, shift, k, roster, team, shifts, unavailable,
                                                 settings["min_rust_uren"])
                if problem is None:
                    here = roster[(roster["datum"] == d) & (roster["dienst"] == shift)]
                    broken = sch.rule_breaks(name, d, shift, k, list(zip(here["naam"], here["kanaal"])), planning_rules)
                    problem = broken[0] if broken else None
                if problem is None:
                    reasons.insert(0, f"could do {shift} on {sch.CHANNEL_LABELS[k]} (drag or + to add)")
                    break
                reasons.append(problem.replace(f"{name} ", ""))
        lines.append(f"{sch.WEEKDAYS[d.weekday()]}: {reasons[0] if reasons else '-'}")
    return "\n".join(lines)


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
        new = new_rule_breaks(roster, updated)
        if new:
            return f"Not possible: {new[0]}", False
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
    new = new_rule_breaks(roster, updated, include_allowed=True)
    blocking = [p for p in new if not manual_ok(p)]
    if blocking:
        return f"Not possible: {blocking[0]}", False
    persist("schedule", storage.clean_roster(merge_range(storage.load_roster(), updated, period)))
    reset_base("schedule", updated)
    if new:  # mag wel, maar laat zien welke planregel je doorbreekt
        return f"{message} — note: {new[0].split(' (')[0].lower()}", True
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
        # Lege plekken in wat al gepland is (week én maand met een rooster): rode "open"-chip in de kalender.
        missing = {}
        if not roster.empty:
            planned_months = set(roster["datum"].map(lambda d: (d.year, d.month)))
            planned_weeks = set(roster["datum"].map(sch.week_key))
            planned_days = [d for d in period if (d.year, d.month) in planned_months and sch.week_key(d) in planned_weeks]
            coverage = sch.check_coverage(roster, rules, calendar[calendar["datum"].isin(planned_days)], planning_rules)
            label_to_channel = {v: k for k, v in sch.CHANNEL_LABELS.items()}
            for c in coverage[coverage["status"] == sch.STATUS_SHORT].itertuples():
                why = f"{c.dagtype}" + (f" + {c.reden}" if c.reden else "")
                missing.setdefault((c.datum, c.dienst), {})[label_to_channel.get(c.kanaal, c.kanaal)] = (c.nodig - c.ingepland, why)
        # Wat nu al niet klopt in het rooster (bijv. te weinig rust) krijgt een rode rand in de kalender.
        flags = {}
        for c in sch.check_conflicts(roster, team, shifts, unavailable, settings["min_rust_uren"],
                                     person_rules=planning_rules).itertuples():
            if pd.notna(c.datum) and not manual_ok(c.probleem):
                flags.setdefault((c.datum, c.naam), []).append(c.probleem)
        html = calendar_view.render(
            games, period, dict(zip(calendar["datum"], calendar["dagtype"])), urls,
            now=pd.Timestamp.now(tz=matches.LOCAL_TZ).to_pydatetime(), clubs=clubs, league_urls=league_urls,
            events=events_by_day, roster=roster, flags=flags, missing=missing, explain=explain_hours,
            # Alleen wie meedoet in het rooster (Auto schedule aan): niet Tim/Rogier of WomenFC als Auto schedule uit staat.
            contracts={n: (0 if z else c) for n, c, z, a in
                       zip(active["naam"], active["contracturen"], active["nuluren"], active["auto"]) if a},
            away={n: {d for r in g.itertuples() if not r.deel for d in period if r.van <= d <= r.tot}
                  for n, g in unavailable.groupby("naam")},
            shift_hours={r.dienst: (lambda w: (w[1] - w[0]).total_seconds() / 3600)(sch.shift_window(start, r.start, r.eind))
                         for r in shifts.itertuples()},
        )
        result = match_calendar(data={"html": html, "people": list(active["naam"]), "channels": sch.CHANNEL_LABELS,
                                      "open_candidates": open_candidates(missing)},
                                key="match_calendar",
                                on_move_change=lambda: None)
        if result.move:
            st.session_state["moved"] = apply_move(result.move)
            st.rerun()
