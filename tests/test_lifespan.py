from _shop import Customer, Sale, Spend
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.testclient import TestClient

from fastapi_gnitz import Db, lifespan


def test_endpoints_read_and_write_through_the_apps_database(server, client):
    app = FastAPI(lifespan=lifespan(server, schema=client.schema, create=[Customer, Sale, Spend]))

    @app.post("/customers", status_code=201)
    async def add_customer(customer: Customer, db: Db) -> Customer:
        await db.insert(customer)
        return customer

    @app.post("/sales", status_code=201)
    async def add_sale(sale: Sale, db: Db) -> None:
        await db.insert(sale)

    @app.get("/customers/{customer_id}")
    async def customer(customer_id: int, db: Db) -> Customer:
        found = await db.get(Customer, customer_id)
        if found is None:
            raise HTTPException(404)
        return found

    @app.get("/spend")
    async def spend(db: Db) -> list[Spend]:
        return await db.all(Spend)

    ann = {"id": 1, "name": "ann", "country": "DE", "tier": None}
    with TestClient(app) as http:
        assert http.post("/customers", json=ann).status_code == 201
        assert http.post("/customers", json={"id": 2}).status_code == 422
        for id_, total in ((1, "10.50"), (2, "4.50")):
            body = {"id": id_, "customer_id": 1, "status": "paid", "total": total, "qty": 1}
            assert http.post("/sales", json=body).status_code == 201
        assert http.get("/customers/1").json() == ann
        assert http.get("/customers/2").status_code == 404
        assert http.get("/spend").json() == [{"customer_id": 1, "orders": 2, "spent": "15.00"}]

    row = app.openapi()["components"]["schemas"]["Spend"]
    assert list(row["properties"]) == ["customer_id", "orders", "spent"]


def test_a_websocket_follows_a_view(server, client):
    app = FastAPI(lifespan=lifespan(server, schema=client.schema, create=[Customer, Sale, Spend]))

    @app.websocket("/spend")
    async def spend(socket: WebSocket, db: Db):
        await socket.accept()
        async for delta in db.changes(Spend, every=0.001):
            await socket.send_json([row.model_dump(mode="json") for row in delta.added])

    with TestClient(app) as http, http.websocket_connect("/spend") as socket:
        assert socket.receive_json() == []
        client.execute_sql(
            "INSERT INTO customer VALUES (1, 'ann', 'DE', NULL); "
            "INSERT INTO sale VALUES (1, 1, 'paid', 10.50, 1)"
        )
        assert socket.receive_json() == [{"customer_id": 1, "orders": 1, "spent": "10.50"}]
