from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.schemas.sharepoint import SharePointSyncRequest
from app.core.settings import Settings
from app.ingestion.sharepoint import (
    SharePointFileSyncRunner,
    SharePointSyncStateStore,
    SharePointTransferService,
)
from app.ingestion.sharepoint.httpx_graph_adapter import _normalize_sites_search
from app.ingestion.sharepoint.sharepoint_sync_service import SharePointSyncService


def _make_settings(
    *,
    container_name: str = "documents",
    default_blob_container: str | None = None,
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
                request_timeout_seconds=10.0,
                download_chunk_size_bytes=1024,
            ),
            blob_storage=SimpleNamespace(container_name=container_name),
            azure_tenant_id="test-tenant-id",
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


def _make_service(
    settings: Settings | None = None,
    blob_service: MagicMock | None = None,
    logger: MagicMock | None = None,
    *,
    sites_repo=None,
) -> SharePointSyncService:
    resolved_settings = settings or _make_settings()
    resolved_blob_service = blob_service or MagicMock()
    resolved_logger = logger or MagicMock()
    adapter = MagicMock()
    factory = MagicMock()
    factory.create_async = AsyncMock(return_value=adapter)
    factory.close = AsyncMock()

    service = SharePointSyncService(
        resolved_settings,
        resolved_blob_service,
        resolved_logger,
        graph_adapter_factory=factory,
        sites_repo=sites_repo,
        state_store=SharePointSyncStateStore(
            blob_service=resolved_blob_service,
            sites_repo=sites_repo,
            build_state_id=SharePointSyncService._build_state_id,
            build_site_id=SharePointSyncService._build_site_id,
        ),
        file_sync_runner=SharePointFileSyncRunner(
            logger=resolved_logger,
            build_state_id=SharePointSyncService._build_state_id,
        ),
        transfer_service=SharePointTransferService(
            blob_service=resolved_blob_service,
            graph_base_url=resolved_settings.sharepoint.graph_base_url,
            download_chunk_size_bytes=resolved_settings.sharepoint.download_chunk_size_bytes,
        ),
    )
    service._test_adapter = adapter  # type: ignore[attr-defined]
    service._test_factory = factory  # type: ignore[attr-defined]
    return service


@pytest.mark.unit
def test_resolve_container_prefers_request_then_default_then_blob() -> None:
    blob_service = MagicMock()
    logger = MagicMock()

    svc = _make_service(
        _make_settings(container_name="docs-default", default_blob_container="sp-default"),
        blob_service,
        logger,
    )

    assert svc._resolve_container(_make_request(destination_container="explicit")) == "explicit"
    assert svc._resolve_container(_make_request()) == "sp-default"

    svc2 = _make_service(_make_settings(container_name="docs-default"), blob_service, logger)
    assert svc2._resolve_container(_make_request()) == "docs-default"


@pytest.mark.unit
def test_resolve_site_prefers_request_then_env() -> None:
    svc = _make_service(
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
    svc = _make_service(_make_settings(), MagicMock(), MagicMock())

    with pytest.raises(ValueError, match="site_hostname and site_path"):
        svc._resolve_site(_make_request(site_hostname=None, site_path=None))


@pytest.mark.unit
@pytest.mark.parametrize(
    "site_name,expected",
    [
        ("IRISSoftware KMAutomation KM01", "KM01"),
        ("IRISSoftware KMAutomation KM-02", "KM02"),
        ("IRISSoftwareGBR KMAutomation KM05", "KM05"),
        ("BainCaseAssistant", ""),
        ("", ""),
    ],
)
def test_extract_case_code_from_site_name(site_name: str, expected: str) -> None:
    assert SharePointSyncService._extract_case_code(site_name) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "search,expected",
    [
        ("", "*"),
        ("   ", "*"),
        ("*", "*"),
        ("IRIS", "IRIS*"),
        ("IRIS*", "IRIS*"),
    ],
)
def test_normalize_sites_search(search: str, expected: str) -> None:
    assert _normalize_sites_search(search) == expected


@pytest.mark.unit
def test_build_sharepoint_metadata_includes_case_code() -> None:
    svc = _make_service(_make_settings(), MagicMock(), MagicMock())

    metadata = svc._build_sharepoint_metadata(
        site_name="IRISSoftware KMAutomation KM01",
        case_code="KM01",
        library_name="Documents",
        site_id="site-1",
        drive_id="drive-1",
        file_entry={
            "name": "a.docx",
            "size": 15,
            "lastModifiedDateTime": "2026-05-29T09:00:00Z",
            "_relative_path": "folder/a.docx",
        },
    )

    assert metadata["sp_site_name"] == "IRISSoftware KMAutomation KM01"
    assert metadata["sp_case_code"] == "KM01"
    assert metadata["sp_file_size_bytes"] == "15"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_copies_files_and_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    logger = MagicMock()

    svc = _make_service(_make_settings(), blob_service, logger)
    adapter = svc._test_adapter  # type: ignore[attr-defined]

    adapter.resolve_site_info = AsyncMock(return_value=("site-1", "IRISSoftware KMAutomation KM01"))
    adapter.resolve_drive_id = AsyncMock(return_value="drive-1")
    adapter.resolve_folder_item_id = AsyncMock(return_value="folder-1")

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

    async def fake_iter(drive_id, folder_item_id):
        for entry in (file_a, file_b):
            yield entry

    adapter.iter_files = fake_iter
    copy_download_mock = AsyncMock()
    monkeypatch.setattr(svc, "_copy_download_url_to_blob", copy_download_mock)

    result = await svc.sync_site(_make_request())

    assert result.discovered == 2
    assert result.copied == 2
    assert result.failed == 0
    assert result.destination_container == "documents"
    assert [item.blob_name for item in result.items] == [
        "MySite/Documents/a.docx",
        "MySite/Documents/b.pdf",
    ]
    assert copy_download_mock.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_records_failed_files(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    svc = _make_service(_make_settings(), blob_service, MagicMock())
    adapter = svc._test_adapter  # type: ignore[attr-defined]

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

    async def fake_iter(drive_id, folder_item_id):
        yield file_a

    adapter.resolve_site_info = AsyncMock(return_value=("site-1", "My Site KM01"))
    adapter.resolve_drive_id = AsyncMock(return_value="d")
    adapter.resolve_folder_item_id = AsyncMock(return_value="f")
    adapter.iter_files = fake_iter
    monkeypatch.setattr(svc, "_copy_download_url_to_blob", AsyncMock(side_effect=RuntimeError("boom")))

    result = await svc.sync_site(_make_request())

    assert result.failed == 1
    assert result.copied == 0
    assert result.items[0].status == "failed"
    assert "boom" in (result.items[0].reason or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_falls_back_to_graph_content_when_download_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    svc = _make_service(_make_settings(), blob_service, MagicMock())
    adapter = svc._test_adapter  # type: ignore[attr-defined]

    file_a = {
        "id": "a",
        "name": "a.txt",
        "size": 5,
        "file": {},
        "parentReference": {"path": "/drive/root:"},
        "_source_path": "/drive/root:/a.txt",
        "_relative_path": "a.txt",
    }

    async def fake_iter(drive_id, folder_item_id):
        yield file_a

    adapter.resolve_site_info = AsyncMock(return_value=("site-1", "My Site KM01"))
    adapter.resolve_drive_id = AsyncMock(return_value="d")
    adapter.resolve_folder_item_id = AsyncMock(return_value="f")
    adapter.iter_files = fake_iter
    adapter.download_file_content = AsyncMock(return_value=b"hello")
    blob_service.upload_artifact = AsyncMock(return_value="https://blob.example/a")

    result = await svc.sync_site(_make_request())

    assert result.copied == 1
    assert result.failed == 0
    blob_service.upload_artifact.assert_awaited_once()
    assert blob_service.upload_artifact.await_args is not None
    metadata = blob_service.upload_artifact.await_args.kwargs["metadata"]
    assert metadata["sp_case_code"] == "KM01"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_falls_back_to_drive_item_content_when_download_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    svc = _make_service(_make_settings(), blob_service, MagicMock())
    adapter = svc._test_adapter  # type: ignore[attr-defined]

    async def fake_iter(drive_id, folder_item_id):
        yield {
            "id": "item-123",
            "name": "a.txt",
            "size": 5,
            "file": {},
            "parentReference": {"path": "/x"},
            "_source_path": "/x/a.txt",
            "_relative_path": "a.txt",
        }

    adapter.resolve_site_info = AsyncMock(return_value=("site-1", "My Site KM01"))
    adapter.resolve_drive_id = AsyncMock(return_value="d")
    adapter.resolve_folder_item_id = AsyncMock(return_value="f")
    adapter.iter_files = fake_iter
    adapter.download_file_content = AsyncMock(return_value=b"hello")
    blob_service.upload_artifact = AsyncMock(return_value="https://blob.example/a")

    result = await svc.sync_site(_make_request())

    assert result.copied == 1
    assert result.failed == 0
    blob_service.upload_artifact.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_payload_site_and_library_override_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    blob_service = MagicMock()
    blob_service.upload_artifact_stream = AsyncMock()
    svc = _make_service(
        _make_settings(
            site_hostname="env.sharepoint.com",
            site_path="/sites/EnvSite",
            library_name="EnvLibrary",
        ),
        blob_service,
        MagicMock(),
    )
    adapter = svc._test_adapter  # type: ignore[attr-defined]

    captured: dict[str, str | None] = {
        "site_id": None,
        "library_name": None,
    }

    adapter.resolve_site_info = AsyncMock(return_value=("site-123", "Payload Site KM05"))

    async def fake_drive(request, site_id, library_name) -> str:
        captured["site_id"] = site_id
        captured["library_name"] = library_name
        return "drive-1"

    adapter.resolve_drive_id = fake_drive
    adapter.resolve_folder_item_id = AsyncMock(return_value="folder-1")

    async def fake_iter(drive_id, folder_item_id):
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

    adapter.iter_files = fake_iter
    monkeypatch.setattr(svc, "_copy_download_url_to_blob", AsyncMock())

    await svc.sync_site(
        _make_request(
            site_hostname="payload.sharepoint.com",
            site_path="/sites/PayloadSite",
            library_name="PayloadLibrary",
        )
    )

    assert captured == {
        "site_id": "site-123",
        "library_name": "PayloadLibrary",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_site_members_returns_projected_members(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _make_service(_make_settings(), MagicMock(), MagicMock(), sites_repo=MagicMock())
    adapter = svc._test_adapter  # type: ignore[attr-defined]
    adapter.get_site_members = AsyncMock(
        return_value=[
            {
                "member_id": "member-1",
                "display_name": "Member One",
                "email": "member.one@contoso.com",
                "role": "owner",
                "source": "graph-group-transitive-user",
            }
        ]
    )

    result = await svc.get_site_members(
        site_hostname="contoso.sharepoint.com",
        site_path="/sites/site-one",
        tenant_id="tenant-1",
    )

    assert len(result) == 1
    assert result[0]["member_id"] == "member-1"
    assert result[0]["display_name"] == "Member One"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_site_prefers_delegated_graph_token(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _make_service(_make_settings(), MagicMock(), MagicMock())
    adapter = svc._test_adapter  # type: ignore[attr-defined]
    factory = svc._test_factory  # type: ignore[attr-defined]
    adapter.resolve_site_info = AsyncMock(return_value=("site-123", "Delegated Site"))
    adapter.resolve_drive_id = AsyncMock(return_value="drive-1")
    adapter.resolve_folder_item_id = AsyncMock(return_value="folder-1")

    async def fake_iter(drive_id, folder_item_id):
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

    adapter.iter_files = fake_iter
    monkeypatch.setattr(svc, "_copy_download_url_to_blob", AsyncMock())

    await svc.sync_site(
        _make_request(),
        delegated_graph_access_token="delegated-token-abc",
    )

    factory.create_async.assert_any_await("delegated-token-abc")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_member_sites_returns_member_sites(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _make_service(_make_settings(), MagicMock(), MagicMock(), sites_repo=MagicMock())
    adapter = svc._test_adapter  # type: ignore[attr-defined]
    adapter.get_member_sites = AsyncMock(
        return_value=[
            {
                "site_id": "site-1",
                "site_name": "Site One",
                "web_url": "https://contoso.sharepoint.com/sites/site-one",
            }
        ]
    )

    result = await svc.get_member_sites(
        user_id="member-1",
        search="*",
        max_results=50,
        tenant_id="tenant-1",
    )

    assert len(result) == 1
    assert result[0]["site_id"] == "site-1"
    assert result[0]["site_name"] == "Site One"
