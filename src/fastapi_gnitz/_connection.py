"""The app's database, held by the lifespan and injected into endpoints."""

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from starlette.requests import HTTPConnection
from starlette.types import StatelessLifespan

from fastapi_gnitz._database import Database
from fastapi_gnitz._relation import Relation
from fastapi_gnitz._view import View


def lifespan(
    target: str,
    *,
    schema: str = "public",
    create: Sequence[type[Relation]] = (),
    mirror: str | os.PathLike[str] | None = None,
) -> StatelessLifespan[FastAPI]:
    """A FastAPI lifespan that holds one `Database` on `target` for the life of
    the app, and creates the relations in `create` when the app starts, as
    `Database.create` does.

    Given the directory `mirror`, it holds a copy there of each view in `create`
    that declares `__delta__`, as `Database.mirror` does.

    The database is opened inside the lifespan, so it is bound to the loop
    that serves the app's requests — a `gnitz.aio` connection works on no other.
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with Database(target, schema=schema) as db:
            await db.create(*create)
            if mirror is not None:
                copied = (v for v in create if issubclass(v, View) and v.__delta__ is not None)
                await db.mirror(mirror, *copied)
            app.state.gnitz = db
            yield

    return _lifespan


async def get_db(http: HTTPConnection) -> Database:
    """The app's database, for an HTTP or a WebSocket endpoint."""
    return http.app.state.gnitz


Db = Annotated[Database, Depends(get_db)]
