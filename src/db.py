"""Verbinding met de Azure SQL-database met wedstrijden (via ODBC Driver 18).

Inloggen gaat met de SQL-gebruiker uit .env (MATCH_DB_USERNAME / MATCH_DB_PASSWORD).
"""

import pyodbc

from config import MATCH_DB_DATABASE, MATCH_DB_SERVER, Secrets

DRIVER = "ODBC Driver 18 for SQL Server"


def _odbc_value(value: str) -> str:
    """Zet een waarde tussen {} zodat tekens als ; en = in het wachtwoord geen kwaad kunnen."""
    return "{" + value.replace("}", "}}") + "}"


def connect() -> pyodbc.Connection:
    return pyodbc.connect(
        f"DRIVER={_odbc_value(DRIVER)};"
        f"SERVER=tcp:{MATCH_DB_SERVER},1433;"
        f"DATABASE={MATCH_DB_DATABASE};"
        f"UID={_odbc_value(Secrets.MATCH_DB_USERNAME)};"
        f"PWD={_odbc_value(Secrets.MATCH_DB_PASSWORD)};"
        "Encrypt=yes;TrustServerCertificate=no;",
        timeout=30,
    )
