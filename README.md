# fastapi-gnitz

FastAPI integration for [Gnitz](https://github.com/Ed-von-Schleck/gnitz), a SQL
database whose views are all materialized and incrementally updated.

Pre-alpha. Requires Python 3.14 and Linux, as the `gnitz` client does.

## Usage

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
`Connection` injects it into an endpoint.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests run against a real `gnitz-server`, which the `gnitz` package on PyPI
does not ship. They take `GNITZ_SERVER_BIN`, else a `gnitz-server` on `PATH`,
else `../gnitzdb/gnitz-server`, and are skipped when none exists.

## License

MIT or Apache-2.0, at your option.
