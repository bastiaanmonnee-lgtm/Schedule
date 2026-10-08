"""Inloggen met streamlit-authenticator, net als in de expense-claim-generator.

De gebruikers staan in een YAML-bestand (wachtwoorden als bcrypt-hash):

    credentials:
      usernames:
        bastiaan:
          name: Bastiaan
          email: bastiaan.monnee@by433.com
          password: <bcrypt-hash>
          role: admin            # admin = alles; anders alleen de eigen pagina's
          employee: ""           # naam zoals op de Employees-pagina (bijv. "Karel")
    cookie:
      name: schedule_auth
      key: <willekeurige sleutel>
      expiry_days: 7

Waar de gebruikers vandaan komen:
- lokaal: auth/config.yaml in de projectmap (of het pad in AUTH_CONFIG_FILE), als die bestaat;
- anders: dezelfde logins als de expense-claim-generator (Datascience Blob Storage,
  container expense-claim-app, auth/config.yaml). Die worden hier alleen gelezen; gebruikers
  beheer je met de scripts van de expense-claim-generator.

Rechten: voorlopig mag iedereen die kan inloggen alles (SCHEDULE_ADMINS="*"). Later beperken
met de omgevingsvariabele SCHEDULE_ADMINS="a@by433.com,b@by433.com": iedereen anders ziet dan
alleen zijn eigen rooster (koppeling met de Employees-pagina via `employee`, anders de voornaam).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent.parent
LOCAL_FILE = Path(os.environ.get("AUTH_CONFIG_FILE") or ROOT / "auth" / "config.yaml")
AUTH_CONTAINER = os.environ.get("AUTH_CONTAINER", "expense-claim-app")  # gedeeld met de expense-claim-generator
AUTH_BLOB = "auth/config.yaml"
COOKIE_NAME = "schedule_auth"  # eigen cookie: inloggen hier logt je niet in bij de expense-tool
SCHEDULE_ADMINS = {a.strip().lower() for a in os.environ.get("SCHEDULE_ADMINS", "*").split(",") if a.strip()}


@dataclass
class User:
    username: str
    name: str
    role: str
    email: str
    employee: str  # naam op de Employees-pagina
    is_admin: bool


def _container():
    from azure.storage.blob import BlobServiceClient

    from config import Secrets

    name = Secrets.ABS_STORAGE_ACCOUNT_NAME_DS
    key = Secrets.ABS_STORAGE_ACCOUNT_KEY_DS
    return BlobServiceClient(f"https://{name}.blob.core.windows.net", credential=key).get_container_client(AUTH_CONTAINER)


def read_config() -> dict:
    if LOCAL_FILE.exists():  # eigen logins om lokaal te testen
        return yaml.safe_load(LOCAL_FILE.read_text(encoding="utf-8"))
    return yaml.safe_load(_container().download_blob(AUTH_BLOB).readall())


@st.cache_data(ttl=300)  # nieuwe gebruikers kunnen binnen 5 minuten inloggen
def load_config() -> dict:
    return read_config()


def require_login() -> tuple[object, User]:
    """Toon het inlogscherm tot iemand is ingelogd; daarna (authenticator, gebruiker)."""
    import streamlit_authenticator as stauth

    try:
        config = load_config()
    except Exception as exc:  # geen config: lokaal nog niet aangemaakt of geen toegang tot Blob Storage
        st.error(f"Could not load the logins ({type(exc).__name__}: {exc}). They come from the expense-claim-generator "
                 "(Datascience Blob Storage); check the Azure settings in .env.")
        st.stop()

    authenticator = stauth.Authenticate(
        config["credentials"],
        COOKIE_NAME,
        config["cookie"]["key"],
        config["cookie"]["expiry_days"],
    )
    authenticator.login(location="main")
    if st.session_state.get("authentication_status") is False:
        st.error("Username or password is incorrect.")
        st.stop()
    if st.session_state.get("authentication_status") is None:
        st.stop()

    username = st.session_state.get("username")
    info = config["credentials"]["usernames"].get(username, {})
    name = info.get("name", username)
    email = info.get("email", "")
    user = User(
        username=username,
        name=name,
        role=info.get("role", "user"),
        email=email,
        employee=info.get("employee") or str(name).split()[0],
        is_admin="*" in SCHEDULE_ADMINS or bool({str(username).lower(), str(email).lower()} & SCHEDULE_ADMINS),
    )
    return authenticator, user
