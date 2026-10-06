"""FastAPI integration for Gnitz: tables and views declared as Pydantic models, and
the app's database, which reads and writes them as those models.
"""

from fastapi_gnitz._connection import Db, get_db, lifespan
from fastapi_gnitz._database import Database, Delta, Session, Transaction
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
    "Cross",
    "Database",
    "Db",
    "Delta",
    "Exists",
    "NotExists",
    "NotNull",
    "PrimaryKey",
    "Session",
    "Table",
    "Transaction",
    "View",
    "coalesce",
    "count",
    "count_distinct",
    "ddl",
    "desc",
    "get_db",
    "is_null",
    "lifespan",
    "link",
    "max_",
    "min_",
    "row_number",
    "sql",
    "sum_",
]
