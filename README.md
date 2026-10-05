# fastapi-gnitz

FastAPI integration for [Gnitz](https://github.com/Ed-von-Schleck/gnitz), a SQL
database whose views are all materialized and incrementally updated.

Pre-alpha. Requires Python 3.14 and Linux, as the `gnitz` client does.

## Declaring tables and views

Tables and views are Pydantic models. A table is the write side; a view is what
an API reads and returns, and its SQL is generated from its class.

```python
from decimal import Decimal
from typing import Annotated, Literal

from annotated_types import Gt
from pydantic import Field

from fastapi_gnitz import PrimaryKey, Table, View, count, ddl, link, sum_


class Customer(Table):
    id: Annotated[int, PrimaryKey]
    name: str


class Sale(Table):
    id: Annotated[int, PrimaryKey]
    customer_id: int
    status: str
    total: Decimal = Field(max_digits=12, decimal_places=2)
    customer = link(Customer, via="customer_id")


class CustomerStats(View[Sale]):
    customer: str = Sale.customer.name
    status: Literal["paid"]
    orders: Annotated[int, Gt(1)] = count()
    spent: Decimal = sum_(Sale.total)
```

`ddl(CustomerStats)` is:

```sql
CREATE VIEW customer_stats AS
SELECT sale__customer.name AS customer, sale.status AS status,
       COUNT(*) AS orders, SUM(sale.total) AS spent
FROM sale JOIN customer sale__customer ON sale.customer_id = sale__customer.id
WHERE sale.status = 'paid'
GROUP BY sale__customer.name, sale.status
HAVING COUNT(*) > 1
```

- **Fields.** A bare field is the source column of that name. A field given a
  value is defined by that expression. Once a field is an aggregate, the others
  are the `GROUP BY` keys.
- **Filters.** What a field's type says of every row is the view's filter: a
  `Literal`, or a `Gt` / `Ge` / `Lt` / `Le` bound. It lands in `WHERE` for a
  column, `HAVING` for an aggregate, `QUALIFY` for a window function.
  `__where__` takes any other condition.
- **Links.** `link(Target, via="column")` declares a foreign key and is a path
  in view definitions: `Sale.customer.name` joins `customer`. A nullable `via`
  is a `LEFT JOIN`, and what is read through it must be `| None`.
- **Sources.** `View[A, B]` joins along the link between them, `B | None` as a
  `LEFT JOIN`; `Exists[B]`, `NotExists[B]` and `Cross[B]` are a semi-join, an
  anti-join and a cross join; `View[A | B]` is `UNION ALL`.
- **Composition.** A view can read a view. `__inline__ = True` makes one a CTE
  of the views that read it instead of a relation of its own.
- **By hand.** `__sql__ = "SELECT ..."` is a view's body as written, for what
  none of the above expresses.

A declaration that cannot be right is refused when the class is defined: a field
whose type differs from its source column, a nullable source declared
non-optional, sources nothing relates.

## Connecting

```python
from fastapi import FastAPI
from fastapi_gnitz import Connection, lifespan

app = FastAPI(lifespan=lifespan("/var/run/gnitz.sock"))


@app.get("/items")
async def items(conn: Connection):
    result = await conn.scan(table_id, schema)
    return [row._asdict() for row in result]
```

`lifespan(target)` holds one `gnitz.aio` connection for the life of the app;
`Connection` injects it into an endpoint. The declarations above are not wired
to it yet: creating the relations and reading them as models is still by hand.

## Development

```bash
uv sync
cargo install gnitz@0.1.0 --locked --root .gnitz
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run pre-commit install
```

The pre-commit hook runs `ruff check --fix`, `ruff format` and `ty check`, each
at the version `uv.lock` pins.

The tests run against a real `gnitz-server`, which the `gnitz` package on PyPI
does not ship. `cargo install` builds it from the `gnitz` crate into `.gnitz/`,
at the version of the client in `uv.lock`; the tests use that binary and no
other.

## License

MIT or Apache-2.0, at your option.
