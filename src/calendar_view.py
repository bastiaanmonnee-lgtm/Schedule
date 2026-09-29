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


ASSETS = Path(__file__).resolve().parent / "assets"


@lru_cache
def _starball() -> str:
    """De Champions League-sterrenbal (wit op transparant) als data-URI voor de achtergrond."""
    data = base64.b64encode((ASSETS / "ucl_starball.png").read_bytes()).decode()
    return f"data:image/png;base64,{data}"


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Unbounded:wght@600;800&display=swap');
.mc { --y:#E1FF00; --bg:#000; --card:#111; --card2:#181818; --line:#262626; --grey:#8a8a8a; --white:#fff;
      --display:'Organetto','Unbounded',sans-serif;
      background:var(--bg); color:var(--white); font-family:'Inter',sans-serif; border-radius:20px; padding:26px; }
.mc * { box-sizing:border-box; }
.mc-display { font-family:var(--display); text-transform:uppercase; font-weight:800; letter-spacing:.01em; }

.mc-head { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; flex-wrap:wrap; margin-bottom:22px; }
.mc-title { font-family:var(--display); font-weight:800; font-size:2rem; line-height:1; text-transform:uppercase; }
.mc-stats { display:flex; gap:22px; flex-wrap:wrap; }
.mc-stat { display:flex; flex-direction:column; min-width:64px; }
.mc-stat b { font-family:var(--display); color:var(--y); font-size:1.5rem; line-height:1; }
.mc-stat span { color:var(--grey); font-size:.78rem; margin-top:4px; }

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

/* Champions League-dag */
.mc-day.ucl { border-color:#2a4bff;
              background: radial-gradient(120% 90% at 100% 100%, #1f3dff 0%, #0b1a8c 45%, #031966 100%); }
.mc-day.ucl::after { content:""; position:absolute; left:50%; top:58%; width:88%; aspect-ratio:1;
                     transform:translate(-50%, -50%) rotate(-12deg);
                     background:var(--stars) center / contain no-repeat; opacity:.2; z-index:0; }
.mc-day.ucl .mc-dow { color:#aebcff; }
.mc-day.ucl .mc-type { display:flex; align-items:center; gap:5px; color:#fff; font-weight:700; text-transform:uppercase;
                       letter-spacing:.06em; font-size:.6rem; }
.mc-day.ucl .mc-type::before { content:""; width:15px; height:15px; flex:none;
                               background:var(--stars) center / contain no-repeat; }
.mc-day.ucl.today { border-color:var(--y); }
.mc-day.ucl .mc-card { background:rgba(3,25,102,.35); border-color:rgba(160,180,255,.35); }

.mc-card { border-radius:10px; padding:8px; margin-top:6px; background:#1d1d1d; border:1px solid #2c2c2c;
           transition:border-color .15s; }
.mc-card:hover { border-color:var(--y); }
.mc-time { display:block; text-align:center; font-family:var(--display); font-weight:800; font-size:.85rem; color:var(--y); }
.mc-logos { display:flex; align-items:center; justify-content:center; gap:8px; margin-top:7px; }
.mc-vs { color:var(--grey); font-size:.62rem; font-weight:700; }
.mc-day.ucl .mc-vs { color:#aebcff; }

.mc-logo { display:inline-flex; align-items:center; justify-content:center; object-fit:contain; flex:none; }
.mc-initials { width:var(--s); height:var(--s); border-radius:50%; color:#fff; font-family:var(--display); font-weight:800;
               font-size:calc(var(--s) * .3); }
.mc-empty { text-align:center; color:var(--grey); padding:40px 0; }
.mc-foot { display:flex; justify-content:space-between; color:var(--grey); font-size:.72rem; margin-top:6px; }
.mc-foot b { font-family:var(--display); color:var(--white); font-size:.95rem; }

@media (max-width: 900px) {
  .mc { padding:18px; }
  .mc-grid { grid-template-columns:1fr; }
  .mc-day { min-height:0; }
  .mc-day.nogames { display:none; }
  .mc-day.ucl::after { width:40%; left:auto; right:-6%; top:50%; transform:translateY(-50%) rotate(-12deg); }
}
</style>
"""


def is_champions_league(name) -> bool:
    return "champions league" in str(name or "").lower()


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


def _card(g, urls) -> str:
    home_ids, away_ids = matches.logo_candidates(g)
    tooltip = f"{g.HomeTeam} – {g.AwayTeam} · {g.competitie or ''}"
    return (
        f'<div class="mc-card" title="{escape(tooltip)}"><span class="mc-time">{escape(g.tijd)}</span>'
        f'<div class="mc-logos">{logo(home_ids, g.HomeTeam, urls, 34)}<span class="mc-vs">VS</span>'
        f'{logo(away_ids, g.AwayTeam, urls, 34)}</div></div>'
    )


def _stats(games: pd.DataFrame, clubs: list[str]) -> str:
    items = [(len(games), "matches")]
    if len(clubs) > 1:
        items.append((len(clubs), "clubs"))
    else:
        home = int((games["thuis_uit"] == "Home").sum())
        items += [(home, "home"), (len(games) - home, "away")]
    items.append((int(games["competitie"].map(is_champions_league).sum()), "champions league"))
    return '<div class="mc-stats">' + "".join(
        f'<div class="mc-stat"><b>{n:02d}</b><span>{label}</span></div>' for n, label in items) + "</div>"


def render(games: pd.DataFrame, period: list[date], day_types: dict[date, str],
           urls: dict[int, str], now: datetime, clubs: list[str]) -> str:
    title = clubs[0] if len(clubs) == 1 else "Fixtures"
    stats = _stats(games, clubs) if not games.empty else ""
    head = f'<div class="mc-head"><div class="mc-title">{escape(title)}</div>{stats}</div>'
    if games.empty:
        return CSS + f'<div class="mc">{head}<div class="mc-empty">No matches in this period.</div></div>'

    by_day = {d: list(g.itertuples()) for d, g in games.groupby("datum")}
    ucl_days = {d for d, day_games in by_day.items() if any(is_champions_league(g.competitie) for g in day_games)}
    ucl_days |= {d for d, t in day_types.items() if is_champions_league(t)}

    parts = [CSS, f'<div class="mc" style="--stars:url(&quot;{_starball()}&quot;)">', head]
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
            if d in ucl_days:
                classes.append("ucl")
            if d == now.date():
                classes.append("today")
            elif d < now.date():
                classes.append("past")
            if not day_games:
                classes.append("nogames")
            label = "Champions League" if d in ucl_days else day_types.get(d, "")
            day_type = f'<div class="mc-type">{escape(label)}</div>' if label else ""
            parts.append(
                f'<div class="{" ".join(classes)}"><div class="mc-day-head">'
                f'<span class="mc-dow">{DAYS[d.weekday()]}</span>'
                f'<span class="mc-date">{d.day:02d}</span></div>{day_type}'
                + "".join(_card(g, urls) for g in day_games)
                + "</div>"
            )
        parts.append("</div></div>")
    parts.append('<div class="mc-foot"><span>Times in Amsterdam time (CET/CEST)</span><b>433</b></div></div>')
    return "".join(parts)
