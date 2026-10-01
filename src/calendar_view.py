"""HTML-kalender met wedstrijden in 433-stijl (voor st.html).

433 brand: zwart, wit, neon-geel accent; brede display-letter (Organetto, hier Unbounded als
vervanger) voor koppen en Inter voor tekst. Champions League-dagen krijgen een eigen achtergrond.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import date, datetime, timedelta
from functools import lru_cache
from html import escape
from pathlib import Path

import pandas as pd

import matches

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


MAX_PER_DAY = 5  # meer wedstrijden op een dag: de populairste volgens de volgorde in clubs.py

ASSETS = Path(__file__).resolve().parent / "assets"


# Europese speeldagen: (css-klasse, label, logo in assets/), op volgorde van voorrang als er op één
# dag meerdere zijn (Europa en Conference League spelen allebei op donderdag).
COMPETITIONS = [
    ("ucl", "Champions League", "ucl_starball.png"),
    ("uel", "Europa League", "uel_logo.png"),
    ("uecl", "Conference League", "uecl_logo.png"),
    ("unl", "Nations League", "unl_logo.png"),
]


@lru_cache
def _logo_css(file: str) -> str:
    """Logo (wit op transparant) als CSS-url voor de achtergrond; 'none' als het bestand er niet is."""
    path = ASSETS / file
    if not path.exists():
        return "none"
    return f"url(&quot;data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}&quot;)"


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Unbounded:wght@600;800&display=swap');
.mc { --y:#E1FF00; --bg:#000; --card:#111; --card2:#181818; --line:#262626; --grey:#8a8a8a; --white:#fff;
      --display:'Organetto','Unbounded',sans-serif;
      background:var(--bg); color:var(--white); font-family:'Inter',sans-serif; border-radius:20px; padding:26px; }
.mc * { box-sizing:border-box; }
.mc-display { font-family:var(--display); text-transform:uppercase; font-weight:800; letter-spacing:.01em; }


.mc-week { margin-bottom:26px; }
.mc-week-label { display:flex; align-items:baseline; gap:12px; margin-bottom:10px; }
.mc-week-label .mc-display { font-size:1.05rem; }
.mc-week-label span.range { color:var(--y); font-family:var(--display); font-weight:600; font-size:.8rem; }
.mc-grid { display:grid; grid-template-columns:repeat(7, minmax(0,1fr)); gap:8px; }
.mc-day { position:relative; overflow:hidden; border:1px solid var(--line); border-radius:14px; padding:10px;
          min-height:124px; background:var(--card); }
.mc-day > * { position:relative; z-index:1; }
.mc-day.weekend { background:var(--card2); }
.mc-day.today { border:2px solid var(--y); }
.mc-day.past { opacity:.45; }
.mc-day-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; }
.mc-dow { color:var(--grey); text-transform:uppercase; font-size:.68rem; font-weight:700; letter-spacing:.1em; }
.mc-date { font-family:var(--display); font-weight:800; font-size:1.15rem; }
.mc-day.today .mc-date { color:var(--y); }
.mc-type { color:var(--grey); font-size:.68rem; margin-bottom:6px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

/* Europese speeldagen: Champions League (blauw), Europa League (oranje), Conference League (groen) */
.mc-day.ucl { --logo:var(--ucl-logo); --soft:#aebcff; border-color:#2a4bff;
              background: radial-gradient(120% 90% at 100% 100%, #1f3dff 0%, #0b1a8c 45%, #031966 100%); }
.mc-day.uel { --logo:var(--uel-logo); --soft:#ffc9a3; border-color:#ff6900;
              background: radial-gradient(120% 90% at 100% 100%, #7a3200 0%, #2b1400 50%, #0d0d0d 100%); }
.mc-day.uecl { --logo:var(--uecl-logo); --soft:#b6f5bd; border-color:#00be14;
               background: radial-gradient(120% 90% at 100% 100%, #00731a 0%, #06330c 50%, #0d0d0d 100%); }
.mc-day.unl { --logo:var(--unl-logo); --soft:#bfe6ff; border-color:#3fa9f5;
              background: radial-gradient(120% 90% at 100% 100%, #0b5c8f 0%, #0a2740 50%, #0d0d0d 100%); }
.mc-day.euro::after { content:""; position:absolute; left:50%; top:58%; width:88%; aspect-ratio:1;
                      transform:translate(-50%, -50%) rotate(-12deg);
                      background:var(--logo) center / contain no-repeat; opacity:.2; z-index:0; }
.mc-day.euro .mc-dow { color:var(--soft); }
.mc-day.euro .mc-type { display:flex; align-items:center; gap:5px; color:#fff; font-weight:700; text-transform:uppercase;
                        letter-spacing:.06em; font-size:.6rem; }
.mc-day.euro .mc-type::before { content:""; width:15px; height:15px; flex:none;
                                background:var(--logo) center / contain no-repeat; }
.mc-day.euro.nologo .mc-type::before { display:none; }
.mc-day.euro.today { border-color:var(--y); }
.mc-day.euro .mc-card { background:rgba(0,0,0,.3); border-color:rgba(255,255,255,.2); }
.mc-day.ucl .mc-card { background:rgba(3,25,102,.35); border-color:rgba(160,180,255,.35); }

/* Dag waarop Nederland speelt */
.mc-day.nl { border-color:#ff8a1f;
             background: radial-gradient(120% 90% at 100% 100%, #ff7a00 0%, #e85d00 45%, #a33c00 100%); }
.mc-day.nl::after { content:none; }
.mc-day.nl .mc-dow { color:#ffe2c4; }
.mc-day.nl .mc-type { color:#fff; font-weight:700; text-transform:uppercase; letter-spacing:.06em; font-size:.6rem; }
.mc-day.nl.today { border-color:var(--y); }
.mc-day.nl .mc-card { background:rgba(80,30,0,.35); border-color:rgba(255,210,170,.4); }

.mc-card { border-radius:10px; padding:6px; margin-top:6px; background:#1d1d1d; border:1px solid #2c2c2c;
           transition:border-color .15s; }
.mc-card:hover { border-color:var(--y); }
.mc-time { font-family:var(--display); font-weight:800; font-size:.72rem; color:var(--y); }
.mc-logos { display:flex; align-items:center; justify-content:center; gap:6px; }

.mc-logo { display:inline-flex; align-items:center; justify-content:center; object-fit:contain; flex:none; }
.mc-initials { width:var(--s); height:var(--s); border-radius:50%; color:#fff; font-family:var(--display); font-weight:800;
               font-size:calc(var(--s) * .3); }
/* Meer dan MAX_PER_DAY wedstrijden: de rest verschijnt als je met de muis over de dag gaat */
.mc-card.extra { display:none; }
.mc-day:hover .mc-card.extra { display:block; }
.mc-day:hover .mc-more { display:none; }
/* Special event (tab Special events): het hele vak in 433-geel, tekst en tijden zwart */
.mc-day.event { background:var(--y); border-color:var(--y); color:#000; }
.mc-day.event::after { content:none; }
.mc-day.event .mc-dow, .mc-day.event .mc-date, .mc-day.event .mc-time, .mc-day.event .mc-more { color:#000; }
.mc-day.event .mc-type { display:block; color:#000; font-weight:800; text-transform:uppercase; letter-spacing:.06em;
                         font-size:.6rem; white-space:normal; }
.mc-day.event .mc-type::before { display:none; }
.mc-day.event .mc-card { background:rgba(0,0,0,.08); border-color:rgba(0,0,0,.25); }
.mc-day.event .mc-card:hover { border-color:#000; }
.mc-day.event.today { border-color:#000; }
.mc-more { cursor:default; text-align:center; color:var(--grey); font-size:.68rem; margin-top:6px; }
.mc-day.euro .mc-more, .mc-day.nl .mc-more { color:rgba(255,255,255,.7); }
.mc-empty { text-align:center; color:var(--grey); padding:40px 0; }
.mc-foot { display:flex; justify-content:space-between; color:var(--grey); font-size:.72rem; margin-top:6px; }
.mc-foot b { font-family:var(--display); color:var(--white); font-size:.95rem; }

@media (max-width: 900px) {
  .mc { padding:18px; }
  .mc-grid { grid-template-columns:1fr; }
  .mc-day { min-height:0; }
  .mc-day.nogames { display:none; }
  .mc-day.euro::after { width:40%; left:auto; right:-6%; top:50%; transform:translateY(-50%) rotate(-12deg); }
}
</style>
"""


# Niet-Europese bonden met ook een 'Champions League' (Azië, Afrika, Noord-Amerika, Oceanië).
OTHER_CONFEDERATIONS = ("afc", "caf", "concacaf", "ofc", "asia", "africa", "arab")


def is_champions_league(name) -> bool:
    c = competition_of(name)
    return c is not None and c[0] == "ucl"


def competition_of(name) -> tuple[str, str, str] | None:
    """Welke Europese competitie (uit COMPETITIONS) hoort bij deze competitie- of dagtypenaam?"""
    lowered = str(name or "").lower()
    if any(word in OTHER_CONFEDERATIONS for word in lowered.replace("-", " ").split()):
        return None
    # Conference eerst: de oude naam was 'Europa Conference League'.
    by_match_order = sorted(COMPETITIONS, key=lambda c: c[0] != "uecl")
    return next((c for c in by_match_order if c[1].lower() in lowered), None)


def _initials(name: str) -> str:
    words = [w for w in str(name).replace("-", " ").split() if w.upper() not in {"FC", "CF", "SC", "AC", "AS", "SV"}]
    if not words:
        return str(name)[:3].upper()
    if len(words) == 1:
        return words[0][:3].upper()
    return "".join(w[0] for w in words[:3]).upper()


def _name_color(name: str) -> str:
    hue = int(hashlib.md5(str(name).encode()).hexdigest()[:4], 16) % 360
    return f"hsl({hue} 35% 28%)"


def logo(candidates: list[int], name: str, urls: dict[int, str], size: int) -> str:
    url = next((urls[i] for i in candidates if i in urls), None)
    if url:
        return (f'<img class="mc-logo" src="{escape(url)}" alt="{escape(str(name))}" title="{escape(str(name))}" '
                f'width="{size}" height="{size}" loading="lazy">')
    return (f'<span class="mc-logo mc-initials" title="{escape(str(name))}" '
            f'style="--s:{size}px;background:{_name_color(name)}">{escape(_initials(name))}</span>')


def _card(g, urls, extra: bool = False) -> str:
    home_ids, away_ids = matches.logo_candidates(g)
    tooltip = f"{g.HomeTeam} – {g.AwayTeam} · {g.competitie or ''}"
    return (
        f'<div class="mc-card{" extra" if extra else ""}" title="{escape(tooltip)}">'
        f'<div class="mc-logos">{logo(home_ids, g.HomeTeam, urls, 28)}<span class="mc-time">{escape(g.tijd)}</span>'
        f'{logo(away_ids, g.AwayTeam, urls, 28)}</div></div>'
    )


def _league_logo(day_games, competition, league_urls: dict[str, str]) -> str | None:
    """Het logo uit LeagueLogoAsset van een wedstrijd in deze competitie, als die er is."""
    for g in day_games:
        asset = str(getattr(g, "LeagueLogoAsset", "") or "").strip()
        if competition_of(g.competitie) == competition and asset in league_urls:
            return league_urls[asset]
    return None


def render(games: pd.DataFrame, period: list[date], day_types: dict[date, str],
           urls: dict[int, str], now: datetime, clubs: list[str], league_urls: dict[str, str] | None = None,
           events: dict[date, list[str]] | None = None) -> str:
    if games.empty and not any(d in (events or {}) for d in period):
        return CSS + f'<div class="mc"><div class="mc-empty">No matches in this period.</div></div>'

    by_day = {d: list(g.itertuples()) for d, g in games.groupby("datum")}
    # Per dag de Europese competitie: eerst uit de wedstrijden (hoogste voorrang), anders het dagtype.
    euro_days = {d: competition_of(t) for d, t in day_types.items() if competition_of(t)}
    for d, day_games in by_day.items():
        found = {competition_of(g.competitie) for g in day_games} - {None}
        if found:
            euro_days[d] = min(found, key=COMPETITIONS.index)
    nl_days = {d for d, day_games in by_day.items()
               if any(matches.club_of(team, ["Netherlands"]) and not matches.is_womens_team(team) for g in day_games for team in (g.HomeTeam, g.AwayTeam))}

    logo_vars = ";".join(f"--{cls}-logo:{_logo_css(file)}" for cls, _, file in COMPETITIONS)
    parts = [CSS, f'<div class="mc" style="{logo_vars}">']
    for week_start in period[::7]:
        week_end = week_start + timedelta(days=6)
        parts.append(
            f'<div class="mc-week"><div class="mc-week-label"><span class="mc-display">Week {week_start.isocalendar()[1]}</span>'
            f'<span class="range">{week_start.day} {MONTHS[week_start.month - 1]} – {week_end.day} {MONTHS[week_end.month - 1]}</span></div>'
            '<div class="mc-grid">'
        )
        for offset in range(7):
            d = week_start + timedelta(days=offset)
            day_games = by_day.get(d, [])
            classes = ["mc-day"]
            if d.weekday() >= 5:
                classes.append("weekend")
            day_events = (events or {}).get(d, [])
            style = ""
            # Voorrang: special event (geel) > Nederland (oranje) > Europese speeldag.
            if day_events:
                classes.append("event")
            elif d in nl_days:
                classes.append("nl")
            elif d in euro_days:
                classes += ["euro", euro_days[d][0]]
                # Eigen bestand in assets/ gaat voor, anders het competitielogo uit de database.
                league_logo = None
                if _logo_css(euro_days[d][2]) == "none":
                    league_logo = _league_logo(day_games, euro_days[d], league_urls or {})
                if league_logo:
                    style = f' style="--logo:url(&quot;{escape(league_logo)}&quot;)"'
                elif _logo_css(euro_days[d][2]) == "none":
                    classes.append("nologo")
            if d == now.date():
                classes.append("today")
            elif d < now.date():
                classes.append("past")
            if not day_games and not day_events:
                classes.append("nogames")
            # Alleen tekst op speciale dagen (event, Nederland, Champions League, ...), niet het gewone dagtype.
            # Bijvoorbeeld 'Champions League / Ballon d'Or' als een event op een speciale speeldag valt.
            special = "Netherlands" if d in nl_days else euro_days[d][1] if d in euro_days else ""
            label = " / ".join([special] * bool(special) + day_events)
            day_type = f'<div class="mc-type">{escape(label)}</div>' if label else ""
            top = {id(g) for g in sorted(day_games, key=lambda g: matches.game_popularity(g, clubs))[:MAX_PER_DAY]}
            hidden = len(day_games) - len(top)
            more = f'<div class="mc-more">+{hidden} more</div>' if hidden else ""
            parts.append(
                f'<div class="{" ".join(classes)}"{style}><div class="mc-day-head">'
                f'<span class="mc-dow">{DAYS[d.weekday()]}</span>'
                f'<span class="mc-date">{d.day:02d}</span></div>{day_type}'

                + "".join(_card(g, urls, extra=id(g) not in top) for g in sorted(day_games, key=lambda g: g.aftrap))
                + more + "</div>"
            )
        parts.append("</div></div>")
    parts.append('<div class="mc-foot"><span>Times in Amsterdam time (CET/CEST)</span><b>433</b></div></div>')
    return "".join(parts)
