"""FastAPI integration for Gnitz: tables and views declared as Pydantic models, and
the app's connection to the database.
"""

from fastapi_gnitz._connection import Connection, get_connection, lifespan
from fastapi_gnitz._expr import (
    coalesce,
    count,
    count_distinct,
    desc,
    is_null,
    max_,
    min_,
    row_number,
    sql,
    sum_,
)
from fastapi_gnitz._relation import PrimaryKey, Table, link
from fastapi_gnitz._view import Cross, Exists, NotExists, NotNull, View, ddl

__all__ = [
    "Connection",
    "Cross",
    "Exists",
    "NotExists",
    "NotNull",
    "PrimaryKey",
    "Table",
    "View",
    "coalesce",
    "count",
    "count_distinct",
    "ddl",
    "desc",
    "get_connection",
    "is_null",
    "lifespan",
    "link",
    "max_",
    "min_",
    "row_number",
    "sql",
    "sum_",
]
