import httpx
from fastapi import FastAPI

from fastapi_gnitz import Connection, lifespan


async def test_endpoint_reads_through_the_app_connection(server, client):
    client.execute_sql(
        "CREATE TABLE items (id BIGINT NOT NULL PRIMARY KEY, qty BIGINT NOT NULL); "
        "INSERT INTO items VALUES (1, 10), (2, 20)"
    )
    table_id, schema = client.resolve_table("items")

    app = FastAPI(lifespan=lifespan(server))

    @app.get("/items")
    async def items(conn: Connection):
        return [row._asdict() for row in await conn.scan(table_id, schema)]

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as http,
    ):
        response = await http.get("/items")

    assert response.status_code == 200
    assert sorted(response.json(), key=lambda r: r["id"]) == [
        {"id": 1, "qty": 10},
        {"id": 2, "qty": 20},
    ]
