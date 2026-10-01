"""Clublogo's uit Azure Blob Storage (container media, logos/club/{id}.png).

De app geeft de browser tijdelijke leeslinks (SAS) per logo; er wordt niets gedownload.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Iterable

CONTAINER = "media"
PREFIX = "logos/club/"
LINK_HOURS = 3


def _container():
    from azure.storage.blob import BlobServiceClient

    from config import Secrets

    name = Secrets.ABS_STORAGE_ACCOUNT_NAME_APP_PROD
    key = Secrets.ABS_STORAGE_ACCOUNT_KEY_APP_PROD
    return BlobServiceClient(f"https://{name}.blob.core.windows.net", credential=key).get_container_client(CONTAINER), name, key


def sample_names(limit: int = 8) -> list[str]:
    """Een paar bestandsnamen onder logos/club/ (voor diagnose: klopt het id-formaat?)."""
    container, _, _ = _container()
    names = []
    for blob in container.list_blobs(name_starts_with=PREFIX):
        names.append(blob.name)
        if len(names) >= limit:
            break
    return names


LEAGUE_PREFIX = "logos/league/"


def league_blob(asset: str) -> str | None:
    """Blob-pad voor een LeagueLogoAsset: een pad ('logos/league/12.png') of alleen een id ('12')."""
    asset = str(asset or "").strip()
    if not asset or asset.lower() in {"none", "nan"}:
        return None
    asset = asset.lstrip("/").removeprefix(f"{CONTAINER}/")
    return asset if "/" in asset or "." in asset else f"{LEAGUE_PREFIX}{asset}.png"


def league_logo_urls(assets: Iterable[str]) -> dict[str, str]:
    """{LeagueLogoAsset: link}: volledige links blijven zoals ze zijn, de rest via Blob Storage."""
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    assets = sorted({str(a).strip() for a in assets if league_blob(a)})
    urls = {a: a for a in assets if a.lower().startswith("http")}
    blobs = {a: league_blob(a) for a in assets if a not in urls}
    if not blobs:
        return urls

    container, name, key = _container()
    expiry = datetime.now(timezone.utc) + timedelta(hours=LINK_HOURS)
    with ThreadPoolExecutor(max_workers=8) as pool:
        exists = dict(zip(blobs, pool.map(lambda b: container.get_blob_client(b).exists(), blobs.values())))
    for asset, blob in blobs.items():
        if exists[asset]:
            sas = generate_blob_sas(name, CONTAINER, blob, account_key=key,
                                    permission=BlobSasPermissions(read=True), expiry=expiry)
            urls[asset] = f"{container.url}/{blob}?{sas}"
    return urls


def logo_urls(ids: Iterable[int]) -> dict[int, str]:
    """{id: leeslink} voor de id's waarvan een logo bestaat."""
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    container, name, key = _container()
    ids = sorted(set(ids))

    with ThreadPoolExecutor(max_workers=8) as pool:
        exists = pool.map(lambda i: container.get_blob_client(f"{PREFIX}{i}.png").exists(), ids)
        found = [i for i, ok in zip(ids, exists) if ok]

    expiry = datetime.now(timezone.utc) + timedelta(hours=LINK_HOURS)
    urls = {}
    for i in found:
        blob = f"{PREFIX}{i}.png"
        sas = generate_blob_sas(name, CONTAINER, blob, account_key=key,
                                permission=BlobSasPermissions(read=True), expiry=expiry)
        urls[i] = f"{container.url}/{blob}?{sas}"
    return urls
