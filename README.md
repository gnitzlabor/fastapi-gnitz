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
- **Schemas.** `__schema__ = "shop"` puts a relation in that schema; without
  it, a relation is in the schema the connection resolves names in.

A declaration that cannot be right is refused when the class is defined: a field
whose type differs from its source column, a nullable source declared
non-optional, sources nothing relates.

## Reading and writing

A `Database` is a connection that speaks in the declared relations: rows go in
and come out as their models.

```python
from fastapi_gnitz import Database, desc

async with Database("/var/run/gnitz.sock") as db:
    await db.create(Customer, Sale, CustomerStats)

    await db.insert(Customer(id=1, name="ann"))
    await db.upsert(Sale(id=1, customer_id=1, status="paid", total=Decimal("9.50")))
    await db.delete(Sale, 7, 8)

    sale = await db.get(Sale, 1)  # or None
    stats = await db.all(CustomerStats)
    recent = await db.all(Sale, where=Sale.customer_id == 1, order_by=[desc(Sale.id)], limit=10)

    async with db.transaction() as tx:
        await tx.insert(Sale(id=2, customer_id=1, status="open", total=Decimal("1.00")))
        await tx.delete(Sale, 1)
```

- **`create`** creates what does not exist, in the order given, along with a
  schema a relation declares. What exists is left alone, and refused if its
  columns, their types or its key are not the declared ones. A view that
  exists keeps its definition: `drop` it to change what it selects.
- **`insert`** refuses a row whose key exists; **`upsert`** replaces it. One
  call is one write, whatever tables its rows are of.
- **`get`** reads by primary key. A view has one where it declares the
  `PrimaryKey` gnitz keys it by, as a view grouped by that column does.
- **`all`** takes a `where` and an `order_by` over the relation's own columns.
  What relates two relations is a view.
- **`transaction`** holds what is written through it, and writes it together
  on leaving the block: all of it, or none if a write is refused. An exception
  discards it. No other request's writes are taken in.
- **`client`** is the `gnitz` connection underneath, for anything else.

### Reading a view from a local copy

A view that declares `__delta__` keeps that much of its changes on the server,
and with them a `Database` keeps a copy of the view on local disk:

```python
class CustomerStats(View[Sale]):
    __delta__ = "64MB"
    ...


await db.mirror("/var/lib/app/mirror", CustomerStats)
stats = await db.all(CustomerStats)  # asks the server nothing
```

`all` and `get` then read the copy. It follows the server's view: a commit
that changes the view is in the copy a moment later, so a read just after a
write may not show it yet. Tables, and views without a copy, are read from the
server. The directory belongs to one process, and holds the copies from one run
to the next.

## In an app

```python
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi_gnitz import Db, lifespan

app = FastAPI(
    lifespan=lifespan(
        "/var/run/gnitz.sock",
        create=[Customer, Sale, CustomerStats],
        mirror="/var/lib/app/mirror",
    )
)


@app.post("/sales", status_code=201)
async def add_sale(sale: Sale, db: Db) -> None:
    await db.insert(sale)


@app.get("/customers/{customer_id}")
async def customer(customer_id: int, db: Db) -> Customer:
    found = await db.get(Customer, customer_id)
    if found is None:
        raise HTTPException(404)
    return found


@app.get("/stats")
async def stats(db: Db) -> list[CustomerStats]:
    return await db.all(CustomerStats)
```

`lifespan(target, create=[...])` holds one `Database` for the life of the app
and creates the relations when it starts; `Db` injects it into an endpoint.
Given `mirror`, it keeps a copy of each created view that declares `__delta__`,
so `GET /stats` is answered without a request to gnitz. A table is the request
body it validates, a view the response it documents.

### Following a view

`changes` reads a view's changes as they are committed: first the view's rows,
then what was added and removed whenever it changed.

```python
@app.websocket("/stats")
async def follow_stats(socket: WebSocket, db: Db):
    await socket.accept()
    async for delta in db.changes(CustomerStats):
        await socket.send_json(
            {
                "reset": delta.reset,
                "added": [row.model_dump(mode="json") for row in delta.added],
                "removed": [row.model_dump(mode="json") for row in delta.removed],
            }
        )
```

A `Delta` with `reset` set holds the view's whole value in `added`: the first
one does, and so does one that follows a gap gnitz no longer holds the changes
for. The view declares `__delta__`. gnitz holds the request until the view
changes, so each `changes` has a connection of its own while it is iterated.

## Development

```bash
uv sync
cargo install gnitz@0.1.4 --locked --root .gnitz
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
