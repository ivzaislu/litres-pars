from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .aggregator import LitResAggregator, SeriesNotFoundError
from .catalog import LitResCatalog
from .client import LitResClient


class ResolveSeriesRequest(BaseModel):
    author: str = Field(min_length=1, max_length=300)
    book_title: str = Field(min_length=1, max_length=500)
    series_name: str = Field(min_length=1, max_length=500)


def create_app(
    *,
    service: LitResAggregator | None = None,
    app_token: str | None = None,
    catalog_path: str | Path | None = None,
    cache_ttl_seconds: int | None = None,
) -> FastAPI:
    """Create the app-only aggregator API.

    Run with:
        uvicorn litres_parser.api:create_app --factory --host 0.0.0.0 --port 8000

    Required environment variable in normal server mode:
        LITRES_APP_TOKEN
    """
    expected_token = app_token or os.environ.get("LITRES_APP_TOKEN")
    if not expected_token:
        raise RuntimeError("LITRES_APP_TOKEN is required")

    db_path = str(
        catalog_path
        or os.environ.get("LITRES_DB_PATH")
        or "litres-aggregator.sqlite3"
    )
    ttl = int(
        cache_ttl_seconds
        if cache_ttl_seconds is not None
        else os.environ.get("LITRES_CACHE_TTL_SECONDS", 7 * 24 * 60 * 60)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            app.state.series_service = service
            yield
            return

        catalog = LitResCatalog(db_path)
        client = LitResClient(user_agent="litres-aggregator/0.1")
        aggregator = LitResAggregator(
            client,
            catalog,
            cache_ttl_seconds=ttl,
        )
        app.state.series_service = aggregator
        try:
            yield
        finally:
            await client.aclose()
            catalog.close()

    app = FastAPI(
        title="LitRes Aggregator",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    if service is not None:
        app.state.series_service = service

    async def require_app(
        authorization: str | None = Header(default=None),
    ) -> None:
        prefix = "Bearer "
        supplied = (
            authorization[len(prefix) :]
            if authorization and authorization.startswith(prefix)
            else ""
        )
        if not supplied or not secrets.compare_digest(supplied, expected_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="unauthorized",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def get_service(request: Request) -> LitResAggregator:
        return request.app.state.series_service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/v1/series/{series_id}",
        dependencies=[Depends(require_app)],
    )
    async def get_series(
        series_id: int,
        series_service: LitResAggregator = Depends(get_service),
    ) -> dict[str, Any]:
        try:
            return await series_service.get_series(series_id)
        except SeriesNotFoundError as exc:
            raise HTTPException(status_code=404, detail="series_not_found") from exc

    @app.post(
        "/v1/series/resolve",
        dependencies=[Depends(require_app)],
    )
    async def resolve_series(
        body: ResolveSeriesRequest,
        series_service: LitResAggregator = Depends(get_service),
    ) -> dict[str, Any]:
        try:
            return await series_service.resolve_series(
                author=body.author,
                book_title=body.book_title,
                series_name=body.series_name,
            )
        except SeriesNotFoundError as exc:
            raise HTTPException(status_code=404, detail="series_not_found") from exc

    return app
