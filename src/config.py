"""Secrets uit Azure Key Vault. Elk secret wordt pas opgehaald als het gebruikt wordt."""

import os
from pathlib import Path

from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from dotenv import dotenv_values, load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"  # .env in de projectroot (in Docker via env_file)
load_dotenv(ENV_PATH)

# Elk secret wordt eerst gezocht in .env onder de naam links (bijv. MATCH_DB_USERNAME=...),
# en anders in Key Vault onder de naam rechts (Key Vault -> Secrets in de Azure portal).
# None = alleen uit .env, niet uit Key Vault.
SECRET_NAMES = {
    # Azure Blob Storage (logo's)
    "ABS_STORAGE_ACCOUNT_NAME_APP_PROD": "AppProdAzureBlobStorageAccountName",
    "ABS_STORAGE_ACCOUNT_KEY_APP_PROD": "AppProdAzureBlobStorageAccountKey",
    # Datascience Blob Storage: daar staan de logins van de expense-claim-generator (gedeeld).
    "ABS_STORAGE_ACCOUNT_NAME_DS": "DatascienceAzureBlobStorageAccountName",
    "ABS_STORAGE_ACCOUNT_KEY_DS": "DatascienceAzureBlobStorageAccountKey",
    # SQL-gebruiker voor de wedstrijden (MatchDataOLAP.EventBase).
    # Let op: 'AppProdAzureSql*' werkt NIET op deze server (login failed voor 'Analytics').
    "MATCH_DB_USERNAME": None,
    "MATCH_DB_PASSWORD": None,
}


def _missing_env(attr: str) -> RuntimeError:
    """Foutmelding met alleen de NAMEN uit .env (nooit de waarden)."""
    if not ENV_PATH.exists():
        return RuntimeError(f"{attr} not found: {ENV_PATH} does not exist.")
    names = sorted(dotenv_values(ENV_PATH))
    return RuntimeError(
        f"{attr} missing or empty in {ENV_PATH}.\n"
        f"Names that are in .env: {', '.join(names) or '(none, is the file UTF-8?)'}"
    )


def _server_host(value: str) -> str:
    """'tcp:host,1433' (zoals in Power BI) -> 'host'."""
    return value.strip().removeprefix("tcp:").split(",")[0]


# Standaard uit de Power BI-bron; te overschrijven met MATCH_DB_SERVER / MATCH_DB_DATABASE in .env.
MATCH_DB_SERVER = _server_host(os.environ.get("MATCH_DB_SERVER") or "by433-sqlserver-weu-01.database.windows.net")
MATCH_DB_DATABASE = (os.environ.get("MATCH_DB_DATABASE") or "by433-sql-weu-01").strip()


class _Secrets:
    _client = None

    def __getattr__(self, attr: str) -> str:
        if attr not in SECRET_NAMES:
            raise AttributeError(attr)
        # Staat de waarde onder dezelfde naam in .env, dan wordt Key Vault overgeslagen.
        if os.environ.get(attr):
            return os.environ[attr]
        if SECRET_NAMES[attr] is None:
            raise _missing_env(attr)
        if _Secrets._client is None:
            credentials = ClientSecretCredential(
                client_id=os.environ["AZURE_CLIENT_ID"],
                client_secret=os.environ["AZURE_CLIENT_SECRET"],
                tenant_id=os.environ["AZURE_TENANT_ID"],
            )
            _Secrets._client = SecretClient(vault_url=os.environ["AZURE_VAULT_URL"], credential=credentials)
        value = _Secrets._client.get_secret(SECRET_NAMES[attr]).value
        setattr(self, attr, value)  # cache voor volgende keer
        return value


Secrets = _Secrets()
