from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.exception_handlers import register_exception_handlers
from app.core.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)


def _build_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    class Payload(BaseModel):
        name: str

    @app.get("/bad-request")
    async def bad_request() -> dict[str, Any]:
        raise BadRequestException("bad input")

    @app.get("/conflict")
    async def conflict() -> dict[str, Any]:
        raise ConflictException("conflict detected")

    @app.get("/not-found")
    async def not_found() -> dict[str, Any]:
        raise NotFoundException("missing")

    @app.get("/unauthorized")
    async def unauthorized() -> dict[str, Any]:
        raise UnauthorizedException("no auth")

    @app.get("/validation")
    async def validation() -> dict[str, Any]:
        raise ValidationException("invalid", errors={"field": ["must not be empty"]})

    @app.post("/request-validation")
    async def request_validation(payload: Payload) -> dict[str, Any]:
        return payload.model_dump()

    @app.get("/http-exception")
    async def http_exception() -> dict[str, Any]:
        raise HTTPException(status_code=418, detail="teapot")

    @app.get("/unhandled")
    async def unhandled() -> dict[str, Any]:
        raise RuntimeError("boom")

    return TestClient(app, raise_server_exceptions=False)


def _assert_problem_shape(body: dict[str, Any]) -> None:
    assert "type" in body
    assert "title" in body
    assert "status" in body
    assert "trace_id" in body
    assert "timestamp" in body


def test_bad_request_exception_maps_to_400() -> None:
    client = _build_client()

    response = client.get("/bad-request")

    assert response.status_code == 400
    body = response.json()
    _assert_problem_shape(body)
    assert body["detail"] == "bad input"


def test_conflict_exception_maps_to_409() -> None:
    client = _build_client()

    response = client.get("/conflict")

    assert response.status_code == 409
    body = response.json()
    _assert_problem_shape(body)
    assert body["detail"] == "conflict detected"


def test_not_found_exception_maps_to_404() -> None:
    client = _build_client()

    response = client.get("/not-found")

    assert response.status_code == 404
    body = response.json()
    _assert_problem_shape(body)
    assert body["detail"] == "missing"


def test_unauthorized_exception_maps_to_401() -> None:
    client = _build_client()

    response = client.get("/unauthorized")

    assert response.status_code == 401
    body = response.json()
    _assert_problem_shape(body)
    assert body["detail"] == "no auth"


def test_validation_exception_maps_to_400_with_errors() -> None:
    client = _build_client()

    response = client.get("/validation")

    assert response.status_code == 400
    body = response.json()
    _assert_problem_shape(body)
    assert body["errors"] == {"field": ["must not be empty"]}


def test_request_validation_error_maps_to_422_with_errors() -> None:
    client = _build_client()

    response = client.post("/request-validation", json={"wrong": "value"})

    assert response.status_code == 422
    body = response.json()
    _assert_problem_shape(body)
    assert "name" in body["errors"]


def test_http_exception_status_is_preserved() -> None:
    client = _build_client()

    response = client.get("/http-exception")

    assert response.status_code == 418
    body = response.json()
    _assert_problem_shape(body)
    assert body["detail"] == "teapot"


def test_unhandled_exception_maps_to_500() -> None:
    client = _build_client()

    response = client.get("/unhandled")

    assert response.status_code == 500
    body = response.json()
    _assert_problem_shape(body)
    assert body["detail"] == "boom"
