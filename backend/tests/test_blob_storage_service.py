from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.settings import Settings
from app.services.blob_storage_service import BlobStorageService


@pytest.mark.unit
def test_ensure_client_raises_when_account_url_missing() -> None:
    service = BlobStorageService(
        settings=cast(Settings, SimpleNamespace(blob_storage=SimpleNamespace(account_url=None)))
    )

    with pytest.raises(ValueError, match="account_url"):
        service._ensure_client()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_artifact_creates_container_and_uploads_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_client = Mock(url="https://storage.example/artifacts/file.txt")
    blob_client.upload_blob = AsyncMock()
    container_client = Mock()
    container_client.create_container = AsyncMock()
    container_client.get_blob_client.return_value = blob_client
    service_client = Mock()
    service_client.get_container_client.return_value = container_client

    monkeypatch.setattr("app.services.blob_storage_service.DefaultAzureCredential", Mock(return_value=Mock()))
    monkeypatch.setattr("app.services.blob_storage_service.BlobServiceClient", Mock(return_value=service_client))

    service = BlobStorageService(
        settings=cast(
            Settings,
            SimpleNamespace(blob_storage=SimpleNamespace(account_url="https://storage.example")),
        )
    )

    url = await service.upload_artifact("artifacts", "file.txt", b"payload")

    container_client.create_container.assert_awaited_once()
    blob_client.upload_blob.assert_awaited_once_with(b"payload", overwrite=True)
    assert url == "https://storage.example/artifacts/file.txt"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_sas_url_uses_user_delegation_key(monkeypatch: pytest.MonkeyPatch) -> None:
    service_client = Mock(account_name="mystorage")
    service_client.get_user_delegation_key = AsyncMock(return_value="delegation-key")

    monkeypatch.setattr("app.services.blob_storage_service.generate_blob_sas", Mock(return_value="sas-token"))

    service = BlobStorageService(
        settings=cast(Settings, SimpleNamespace(blob_storage=SimpleNamespace(account_url="unused")))
    )
    service._blob_service_client = service_client

    sas_url = await service.generate_sas_url("artifacts", "folder/file.txt", ttl_minutes=5)

    service_client.get_user_delegation_key.assert_awaited_once()
    assert sas_url == "https://mystorage.blob.core.windows.net/artifacts/folder/file.txt?sas-token"
