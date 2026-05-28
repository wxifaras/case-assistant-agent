from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.schemas.sharepoint import SharePointSyncRequest
from app.core.settings import Settings
from app.ingestion.sharepoint_sync_service import SharePointSyncService


def _make_settings(
    *,
    container_name: str = "documents",
    default_blob_container: str | None = None,
    max_files_per_run: int = 500,
    site_hostname: str | None = None,
    site_path: str | None = None,
    library_name: str | None = None,
) -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            sharepoint=SimpleNamespace(
                graph_base_url="https://graph.example/v1.0",
                graph_scope="https://graph.example/.default",
                site_hostname=site_hostname,
                site_path=site_path,
                library_name=library_name,
                default_blob_container=default_blob_container,
                max_files_per_run=max_files_per_run,
                request_timeout_seconds=10.0,
                download_chunk_size_bytes=1024,
            ),
            blob_storage=SimpleNamespace(container_name=container_name),
        ),
    )


def _make_request(**overrides) -> SharePointSyncRequest:
    base: dict = {
        "site_hostname": "contoso.sharepoint.com",
        "site_path": "/sites/MySite",
        "library_name": "Documents",
    }
    base.update(overrides)
    return SharePointSyncRequest(**base)


@pytest.mark.unit
def test_resolve_container_prefers_request_then_default_then_blob() -> None:
    blob_service = MagicMock()
    logger = MagicMock()

    svc = SharePointSyncService(
        _make_settings(container_name="docs-default", default_blob_container="sp-default"),
        blob_service,
        logger,
    )

    assert svc._resolve_container(_make_request(destination_container="explicit")) == "explicit"
    assert svc._resolve_container(_make_request()) == "sp-default"

    svc2 = SharePointSyncService(_make_settings(container_name="docs-default"), blob_service, logger)
    assert svc2._resolve_container(_make_request()) == "docs-default"


@pytest.mark.unit
def test_resolve_cap_bounds_request_to_hard_cap() -> None:
    svc = SharePointSyncService(_make_settings(max_files_per_run=10), MagicMock(), MagicMock())
    assert svc._resolve_cap(_make_request()) == 10
    assert svc._resolve_cap(_make_request(max_files=5)) == 5
    assert svc._resolve_cap(_make_request(max_files=1000)) == 10


@pytest.mark.unit
def test_resolve_site_prefers_request_then_env() -> None:
    svc = SharePointSyncService(
        _make_settings(site_hostname="env.sharepoint.com", site_path="/sites/EnvSite"),
        MagicMock(),
        MagicMock(),
    )

    request_values = svc._resolve_site(_make_request(site_hostname="req.sharepoint.com", site_path="/sites/Req"))
    env_values = svc._resolve_site(_make_request(site_hostname=None, site_path=None))

    assert request_values == ("req.sharepoint.com", "/sites/Req")
    assert env_values == ("env.sharepoint.com", "/sites/EnvSite")


@pytest.mark.unit
def test_resolve_site_raises_when_missing_from_request_and_env() -> None:
    svc = SharePointSyncService(_make_settings(), MagicMock(), MagicMock())

    with pytest.raises(ValueError, match="site_hostname and site_path"):
        svc._resolve_site(_make_request(site_hostname=None, site_path=None))


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        ("foo", "foo/"),
        ("foo/", "foo/"),
        ("/foo/bar", "foo/bar/"),
    ],
)
def test_normalize_prefix(raw, expected) -> None:
    assert SharePointSyncService._normalize_prefix(raw) == expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_copies_files_and_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    logger = MagicMock()

    svc = SharePointSyncService(_make_settings(), blob_service, logger)

    async def fake_token() -> str:
        return "token"

    async def fake_drive(headers, request, site_hostname, site_path, library_name) -> str:
        return "drive-1"

    async def fake_folder(headers, drive_id, folder_path) -> str:
        return "folder-1"

    file_a = {
        "id": "a",
        "name": "a.docx",
        "size": 10,
        "file": {},
        "parentReference": {"path": "/drive/root:/Cases"},
        "@microsoft.graph.downloadUrl": "https://dl.example/a",
        "_source_path": "/drive/root:/Cases/a.docx",
        "_relative_path": "a.docx",
    }
    file_b = {
        "id": "b",
        "name": "b.pdf",
        "size": 20,
        "file": {},
        "parentReference": {"path": "/drive/root:/Cases"},
        "@microsoft.graph.downloadUrl": "https://dl.example/b",
        "_source_path": "/drive/root:/Cases/b.pdf",
        "_relative_path": "b.pdf",
    }

    async def fake_iter(headers, drive_id, folder_item_id):
        for entry in (file_a, file_b):
            yield entry

    monkeypatch.setattr(svc, "_acquire_graph_token", fake_token)
    monkeypatch.setattr(svc, "_resolve_drive_id", fake_drive)
    monkeypatch.setattr(svc, "_resolve_folder_item_id", fake_folder)
    monkeypatch.setattr(svc, "_iter_files", fake_iter)
    copy_download_mock = AsyncMock()
    monkeypatch.setattr(svc, "_copy_download_url_to_blob", copy_download_mock)

    result = await svc.sync(_make_request(blob_prefix="staging"))

    assert result.discovered == 2
    assert result.copied == 2
    assert result.failed == 0
    assert result.destination_container == "documents"
    assert [item.blob_name for item in result.items] == ["staging/a.docx", "staging/b.pdf"]
    assert copy_download_mock.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_dry_run_does_not_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    svc = SharePointSyncService(_make_settings(), blob_service, MagicMock())

    file_a = {
        "id": "a",
        "name": "a.txt",
        "size": 5,
        "file": {},
        "parentReference": {"path": "/drive/root:"},
        "@microsoft.graph.downloadUrl": "https://dl.example/a",
        "_source_path": "/drive/root:/a.txt",
        "_relative_path": "a.txt",
    }

    async def fake_iter(headers, drive_id, folder_item_id):
        yield file_a

    monkeypatch.setattr(svc, "_acquire_graph_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(svc, "_resolve_drive_id", AsyncMock(return_value="d"))
    monkeypatch.setattr(svc, "_resolve_folder_item_id", AsyncMock(return_value="f"))
    monkeypatch.setattr(svc, "_iter_files", fake_iter)

    result = await svc.sync(_make_request(dry_run=True))

    assert result.discovered == 1
    assert result.copied == 0
    assert result.skipped == 1
    assert result.items[0].status == "dry_run"
    blob_service.upload_artifact_stream.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_records_failed_files(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    svc = SharePointSyncService(_make_settings(), blob_service, MagicMock())

    file_a = {
        "id": "a",
        "name": "a.txt",
        "size": 5,
        "file": {},
        "parentReference": {"path": "/x"},
        "@microsoft.graph.downloadUrl": "https://dl.example/a",
        "_source_path": "/x/a.txt",
        "_relative_path": "a.txt",
    }

    async def fake_iter(headers, drive_id, folder_item_id):
        yield file_a

    monkeypatch.setattr(svc, "_acquire_graph_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(svc, "_resolve_drive_id", AsyncMock(return_value="d"))
    monkeypatch.setattr(svc, "_resolve_folder_item_id", AsyncMock(return_value="f"))
    monkeypatch.setattr(svc, "_iter_files", fake_iter)
    monkeypatch.setattr(svc, "_copy_download_url_to_blob", AsyncMock(side_effect=RuntimeError("boom")))

    result = await svc.sync(_make_request())

    assert result.failed == 1
    assert result.copied == 0
    assert result.items[0].status == "failed"
    assert "boom" in (result.items[0].reason or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_honors_max_files_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    svc = SharePointSyncService(_make_settings(max_files_per_run=1), blob_service, MagicMock())

    def make_entry(name: str) -> dict:
        return {
            "id": name,
            "name": name,
            "size": 1,
            "file": {},
            "parentReference": {"path": "/x"},
            "@microsoft.graph.downloadUrl": f"https://dl.example/{name}",
            "_source_path": f"/x/{name}",
            "_relative_path": name,
        }

    async def fake_iter(headers, drive_id, folder_item_id):
        for n in ("a.txt", "b.txt", "c.txt"):
            yield make_entry(n)

    monkeypatch.setattr(svc, "_acquire_graph_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(svc, "_resolve_drive_id", AsyncMock(return_value="d"))
    monkeypatch.setattr(svc, "_resolve_folder_item_id", AsyncMock(return_value="f"))
    monkeypatch.setattr(svc, "_iter_files", fake_iter)
    monkeypatch.setattr(svc, "_copy_download_url_to_blob", AsyncMock())

    result = await svc.sync(_make_request())

    assert result.discovered == 1
    assert result.copied == 1
    assert any("Max files cap" in w for w in result.warnings)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_falls_back_to_graph_content_when_download_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    svc = SharePointSyncService(_make_settings(), blob_service, MagicMock())

    file_a = {
        "id": "a",
        "name": "a.txt",
        "size": 5,
        "file": {},
        "parentReference": {"path": "/drive/root:"},
        "_source_path": "/drive/root:/a.txt",
        "_relative_path": "a.txt",
    }

    async def fake_iter(headers, drive_id, folder_item_id):
        yield file_a

    monkeypatch.setattr(svc, "_acquire_graph_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(svc, "_resolve_drive_id", AsyncMock(return_value="d"))
    monkeypatch.setattr(svc, "_resolve_folder_item_id", AsyncMock(return_value="f"))
    monkeypatch.setattr(svc, "_iter_files", fake_iter)
    copy_drive_item_mock = AsyncMock()
    monkeypatch.setattr(svc, "_copy_drive_item_to_blob", copy_drive_item_mock)

    result = await svc.sync(_make_request())

    assert result.copied == 1
    assert result.failed == 0
    copy_drive_item_mock.assert_awaited_once_with({"Authorization": "Bearer t"}, "d", "a", "documents", "a.txt")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_falls_back_to_drive_item_content_when_download_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    svc = SharePointSyncService(_make_settings(), blob_service, MagicMock())

    async def fake_iter(headers, drive_id, folder_item_id):
        yield {
            "id": "item-123",
            "name": "a.txt",
            "size": 5,
            "file": {},
            "parentReference": {"path": "/x"},
            "_source_path": "/x/a.txt",
            "_relative_path": "a.txt",
        }

    monkeypatch.setattr(svc, "_acquire_graph_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(svc, "_resolve_drive_id", AsyncMock(return_value="d"))
    monkeypatch.setattr(svc, "_resolve_folder_item_id", AsyncMock(return_value="f"))
    monkeypatch.setattr(svc, "_iter_files", fake_iter)
    copy_drive_item_mock = AsyncMock()
    monkeypatch.setattr(svc, "_copy_drive_item_to_blob", copy_drive_item_mock)

    result = await svc.sync(_make_request())

    assert result.copied == 1
    assert result.failed == 0
    copy_drive_item_mock.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_payload_site_and_library_override_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    svc = SharePointSyncService(
        _make_settings(
            site_hostname="env.sharepoint.com",
            site_path="/sites/EnvSite",
            library_name="EnvLibrary",
        ),
        blob_service,
        MagicMock(),
    )

    captured: dict[str, str | None] = {
        "site_hostname": None,
        "site_path": None,
        "library_name": None,
    }

    async def fake_drive(headers, request, site_hostname, site_path, library_name) -> str:
        captured["site_hostname"] = site_hostname
        captured["site_path"] = site_path
        captured["library_name"] = library_name
        return "drive-1"

    async def fake_folder(headers, drive_id, folder_path) -> str:
        return "folder-1"

    async def fake_iter(headers, drive_id, folder_item_id):
        yield {
            "id": "a",
            "name": "a.txt",
            "size": 1,
            "file": {},
            "parentReference": {"path": "/x"},
            "@microsoft.graph.downloadUrl": "https://dl.example/a",
            "_source_path": "/x/a.txt",
            "_relative_path": "a.txt",
        }

    monkeypatch.setattr(svc, "_acquire_graph_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(svc, "_resolve_drive_id", fake_drive)
    monkeypatch.setattr(svc, "_resolve_folder_item_id", fake_folder)
    monkeypatch.setattr(svc, "_iter_files", fake_iter)
    monkeypatch.setattr(svc, "_copy_download_url_to_blob", AsyncMock())

    await svc.sync(
        _make_request(
            site_hostname="payload.sharepoint.com",
            site_path="/sites/PayloadSite",
            library_name="PayloadLibrary",
        )
    )

    assert captured == {
        "site_hostname": "payload.sharepoint.com",
        "site_path": "/sites/PayloadSite",
        "library_name": "PayloadLibrary",
    }
