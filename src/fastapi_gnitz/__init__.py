"""FastAPI integration for Gnitz.

Tables and views are declared as Pydantic models, and a view's SQL is generated
from its class::

    class Sale(Table):
        id: Annotated[int, PrimaryKey]
        customer_id: int
        status: str
        total: Decimal = Field(max_digits=12, decimal_places=2)
        customer = link(Customer, via="customer_id")

    class CustomerStats(View[Sale]):
        customer_id: int
        status: Literal["paid"]
        orders: int = count()
        spent: Decimal = sum_(Sale.total)

    app = FastAPI(lifespan=lifespan("/var/run/gnitz.sock"))

    @app.get("/stats")
    async def stats(conn: Connection) -> list[CustomerStats]: ...
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
from fastapi_gnitz._relation import NotNull, PrimaryKey, Table, link
from fastapi_gnitz._view import Cross, Exists, NotExists, View, ddl

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
