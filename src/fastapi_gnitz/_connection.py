"""The app's connection to gnitz, held by the lifespan and injected into endpoints."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from gnitz import aio
from starlette.requests import HTTPConnection
from starlette.types import StatelessLifespan


def lifespan(target: str) -> StatelessLifespan[FastAPI]:
    """A FastAPI lifespan that holds one `gnitz.aio` connection to `target` for
    the life of the app.

    The connection is opened inside the lifespan, so it is bound to the loop
    that serves the app's requests — a `gnitz.aio` connection works on no other.
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with aio.connect(target) as conn:
            app.state.gnitz = conn
            yield

    return _lifespan


async def get_connection(http: HTTPConnection) -> aio.AsyncConnection:
    """The app's gnitz connection, for an HTTP or a WebSocket endpoint."""
    return http.app.state.gnitz


Connection = Annotated[aio.AsyncConnection, Depends(get_connection)]
