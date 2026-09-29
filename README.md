# Schedule – 433 social media team ⚽

A Streamlit app that builds the rota for the social media team. How many people are needed depends on the type of day, for example a Champions League night versus a regular matchday. The *Matches* tab shows the fixtures of the clubs you follow, taken from `MatchDataOLAP.EventBase` in Azure SQL, with club logos from Azure Blob Storage.

## Getting started

Copy `.env.example` to `.env` and fill in the values:

- `MATCH_DB_SERVER`, `MATCH_DB_DATABASE`, `MATCH_DB_USERNAME`, `MATCH_DB_PASSWORD`: the SQL login for the matches.
- `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_VAULT_URL`: Key Vault, used for the storage account that holds the club logos.

### With Docker

```bash
docker compose up --build
```

The app then runs at http://localhost:8503. The team, calendar and schedule are saved in `./data`. That folder is mounted as a volume, so your data survives a rebuild.

### Without Docker

```bash
pip install -r requirements.txt
streamlit run src/main.py
```

This needs *ODBC Driver 18 for SQL Server* installed. Without it, the *Matches* tab can't load.

## Structure

```
src/
  main.py           Streamlit app (all tabs)
  calendar_view.py  match calendar in 433 style (HTML)
  matches.py        loading and preparing matches
  logos.py          club logos from Blob Storage (temporary read links)
  scheduler.py      automatic scheduling and checks
  storage.py        storage as CSV in data/
  db.py             Azure SQL connection (pyodbc)
  config.py         secrets from .env / Azure Key Vault
  assets/           Champions League starball for the calendar
.streamlit/config.toml  433 theme (black, white, neon yellow)
```

## Tabs

1. **⚽ Matches**: a weekly calendar with the matches of the clubs you follow (by default Barcelona, Real Madrid, Juventus, Manchester City and FC Utrecht). You can type extra clubs into *Clubs*; the list is saved. Each card shows the kick-off time (Amsterdam time) and both logos. Champions League days get their own blue background. Under *Teams* you choose which teams with those names count, for example to leave out youth or women's teams. If some logos can't be found, a *Logos* panel shows which ones. Data is cached for an hour; *Refresh* reloads it straight away.
2. **👥 Team**: names, seniority (Junior / Medior / Senior / Lead), max shifts per week and fixed days off.
3. **📅 Weeks & staffing**: the day type for each day (Champions League, Eredivisie matchday, Transfer Deadline Day, ...). *Quick set* lets you set, say, every Tuesday and Wednesday to Champions League in one go. At the bottom you see the shifts needed per week next to the team's capacity.
4. **⚙️ Staffing rules**: the shifts (times) and, per day type and shift, how many people are needed and how many of them must be senior. This is also where you set the minimum rest between shifts.
5. **🏖️ Time off**: holidays and leave.
6. **🗓️ Schedule**: *Generate proposal* builds a schedule automatically, which you can then edit by hand. The check shows shortages and conflicts, and you can download everything as Excel.

Senior and Lead count as "senior". `data/` and `.env` are in `.gitignore` because they contain names and credentials.

## Deploy

`.github/workflows/build-push-image.yml` builds an image on every push to `main` and pushes it to ACR, like the other projects. It needs these repository secrets: `ACR_SERVER`, `ACR_USERNAME` and `ACR_PASSWORD`.

Note: in a container in Azure, `data/` is lost on every restart. Move storage to Blob Storage before deploying.
