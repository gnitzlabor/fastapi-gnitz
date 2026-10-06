"""The declared relations created, read and written on a real server."""

import asyncio
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

import gnitz
import pytest
from _shop import TABLES, Customer, Employee, Money, Peer, Refund, Sale, Spend, refused
from pydantic import Field

from fastapi_gnitz import Database, PrimaryKey, Table, View, count, desc, link, sum_

ANN = Customer(id=1, name="ann", country="DE", tier=1)
BOB = Customer(id=2, name="bob", country="DE", tier=None)


def sale(id, customer_id, total, status="paid"):
    return Sale(id=id, customer_id=customer_id, status=status, total=Decimal(total), qty=1)


@pytest.fixture
async def shop(db):
    await db.create(*TABLES, Spend)
    await db.insert(ANN, BOB, sale(1, 1, "50.00"), sale(2, 1, "150.00"), sale(3, 2, "400.00"))
    return db


# -- definition ----------------------------------------------------------------


async def test_rows_come_back_as_the_models_that_went_in(db):
    class Everything(Table):
        a: Annotated[int, PrimaryKey]
        b: Annotated[int, PrimaryKey]
        f: float
        s: str | None
        day: date
        at: datetime
        ident: uuid.UUID
        amount: Money

    row = Everything(
        a=1, b=2, f=1.5, s=None, day=date(2026, 1, 2), at=datetime(2026, 1, 2, 3, 4, 5, 678),
        ident=uuid.uuid4(), amount=Decimal("3.50"),
    )  # fmt: skip
    await db.create(Everything)
    await db.insert(row)
    assert await db.all(Everything) == [row]
    assert await db.get(Everything, (1, 2)) == row
    assert await db.get(Everything, (1, 3)) is None


async def test_create_leaves_what_exists_and_refuses_what_differs(shop, server, client):
    await shop.create(*TABLES, Spend)
    assert len(await shop.all(Sale)) == 3

    class FewerColumns(Table):
        __relation__ = "customer"
        id: Annotated[int, PrimaryKey]
        name: str

    class Nullable(Table):
        __relation__ = "customer"
        id: Annotated[int, PrimaryKey]
        name: str | None
        country: str
        tier: int | None

    class OtherScale(Table):
        __relation__ = "refund"
        id: Annotated[int, PrimaryKey]
        sale_id: int
        amount: Decimal = Field(max_digits=12, decimal_places=3)

    class OtherKey(Table):
        __relation__ = "refund"
        id: Annotated[int, PrimaryKey]
        sale_id: Annotated[int, PrimaryKey]
        amount: Money

    class NoScale(Table):
        __relation__ = "refund"
        id: Annotated[int, PrimaryKey]
        sale_id: int
        amount: Decimal

    class OtherType(View):
        __relation__ = "spend"
        __sql__ = "SELECT 1"
        customer_id: int
        orders: int
        spent: str

    # Another connection: this one has not found these relations to be as declared.
    async with Database(server, schema=client.schema) as other:
        with refused(
            r"customer is \(.*, tier int \| None, .*\); declared is \(id int, name str, PRIM"
        ):
            await other.create(FewerColumns)
        with refused(
            r"Nullable: customer is \(id int, name str, .* declared is \(id int, name str \| None"
        ):
            await other.all(Nullable)
        with refused(r"refund is \(.*amount Decimal\(2\), .* declared is \(.*amount Decimal\(3\)"):
            await other.insert(OtherScale(id=9, sale_id=1, amount=Decimal(1)))
        with refused(r"amount Decimal\(2\), .* declared is \(.*amount Decimal\(None\)"):
            await other.all(NoScale)
        with refused(
            r"refund is \(.*PRIMARY KEY \(id\)\); declared is \(.*PRIMARY KEY \(id, sale_id\)"
        ):
            await other.create(OtherKey)
        with refused(r"spend is \(.*spent Decimal\); declared is \(.*spent str\)"):
            await other.all(OtherType)


async def test_drop_removes_a_relation(shop):
    await shop.drop(Spend, Refund)
    with pytest.raises(gnitz.GnitzNotFoundError):
        await shop.all(Spend)
    await shop.create(Refund, Spend)
    assert await shop.all(Refund) == []


async def test_what_is_no_relation_of_the_database_is_refused(shop):
    class Fragment(View[Sale]):
        __inline__ = True
        id: int

    with refused("Fragment is inline"):
        await shop.all(Fragment)
    with refused("is not a table or a view"):
        await shop.all(int)
    for of_its_own in (shop.create, shop.drop, shop.delete):
        with refused("is an alias of Employee"):
            await of_its_own(Peer)
    with refused("is not a table"):
        await shop.delete(Spend, 1)
    with refused("is not a table"):
        await shop.insert(Spend(customer_id=1, orders=1, spent=Decimal(1)))


# -- reads ---------------------------------------------------------------------


async def test_all_filters_orders_and_limits_by_the_relations_columns(shop):
    assert await shop.all(Sale, where=Sale.customer_id == 1, order_by=[Sale.id]) == [
        sale(1, 1, "50.00"),
        sale(2, 1, "150.00"),
    ]
    by_total = await shop.all(Sale, order_by=[desc(Sale.total)], limit=2, offset=1)
    assert [s.id for s in by_total] == [2, 1]
    cheap = await shop.all(Sale, where=(Sale.total < 100) | (Sale.status == "void"))
    assert [s.id for s in cheap] == [1]
    assert await shop.all(Customer, where=Customer.name == "o'hara") == []


async def test_a_condition_past_the_relation_is_refused(shop):
    with refused("where: .* reads past Sale"):
        await shop.all(Sale, where=Sale.customer.name == "ann")
    with refused("where: .* reads past Sale"):
        await shop.all(Sale, where=Customer.id == 1)
    with refused("where: 1 is not an expression"):
        await shop.all(Sale, where=1)
    with refused("where: .* is not an expression over the columns"):
        await shop.all(Sale, where=count() > 1)


async def test_an_alias_reads_the_relation_it_stands_for(db):
    await db.create(Employee)
    await db.insert(Employee(id=1, name="root", manager_id=None, sal=100))
    assert await db.all(Peer, where=Peer.sal > 50) == [
        Peer(id=1, name="root", manager_id=None, sal=100)
    ]


async def test_a_view_is_read_as_its_model_and_by_its_declared_key(shop):
    assert sorted(await shop.all(Spend), key=lambda s: s.customer_id) == [
        Spend(customer_id=1, orders=2, spent=Decimal("200.00")),
        Spend(customer_id=2, orders=1, spent=Decimal("400.00")),
    ]
    assert await shop.get(Spend, 2) == Spend(customer_id=2, orders=1, spent=Decimal("400.00"))
    assert await shop.get(Spend, 3) is None


async def test_a_key_is_what_gnitz_keys_the_relation_by(shop):
    class Paid(View[Sale]):
        id: int
        total: Money

    class ByTotal(View[Sale]):
        id: int
        total: Annotated[Decimal, PrimaryKey]

    await shop.create(Paid)
    with refused("Paid declares no PrimaryKey"):
        await shop.get(Paid, 1)
    with refused("Sale is keyed by id; got"):
        await shop.get(Sale, (1, 2))
    with refused(r"by_total is \(.*PRIMARY KEY \(id\)\); declared is \(.*PRIMARY KEY \(total\)"):
        await shop.create(ByTotal)


# -- writes --------------------------------------------------------------------


async def test_insert_refuses_a_key_that_exists_and_adds_none_of_its_rows(shop):
    with pytest.raises(gnitz.GnitzIntegrityError, match="already exists"):
        await shop.insert(sale(4, 1, "1.00"), sale(1, 1, "2.00"))
    with pytest.raises(gnitz.GnitzIntegrityError, match="Foreign Key violation"):
        await shop.insert(Customer(id=3, name="cy", country="FR", tier=2), sale(5, 777, "1.00"))
    assert len(await shop.all(Sale)) == len(await shop.all(Customer)) + 1 == 3


async def test_upsert_replaces_and_delete_removes(shop):
    await shop.upsert(sale(1, 2, "75.00", status="open"), sale(4, 2, "1.00"))
    assert await shop.get(Sale, 1) == sale(1, 2, "75.00", status="open")
    await shop.delete(Sale, 1, 4, 999)
    assert sorted(s.id for s in await shop.all(Sale)) == [2, 3]
    assert await shop.get(Spend, 2) == Spend(customer_id=2, orders=1, spent=Decimal("400.00"))


async def test_a_transaction_commits_on_leaving_and_discards_on_an_exception(shop):
    async with shop.transaction() as tx:
        await tx.insert(Customer(id=3, name="cy", country="FR", tier=2))
        await tx.insert(sale(4, 3, "9.00"))
        await tx.delete(Sale, 1)
        assert await tx.get(Sale, 4) is None
    assert await shop.get(Sale, 4) == sale(4, 3, "9.00")
    assert await shop.get(Sale, 1) is None

    with pytest.raises(ZeroDivisionError):
        async with shop.transaction() as tx:
            await tx.insert(sale(5, 3, "9.00"))
            raise ZeroDivisionError
    assert await shop.get(Sale, 5) is None

    # A write is refused when the block is left, and then none of them is made.
    with pytest.raises(gnitz.GnitzIntegrityError, match="already exists"):
        async with shop.transaction() as tx:
            await tx.insert(Customer(id=4, name="dee", country="FR", tier=None))
            await tx.insert(sale(2, 3, "1.00"))
    with refused("cannot be interpreted as an integer"):
        async with shop.transaction() as tx:
            await tx.insert(Customer(id=4, name="dee", country="FR", tier=None), sale(6, 3, "1.00"))
            await tx.delete(Sale, (1, 2))
    assert await shop.get(Customer, 4) is None
    await shop.insert(sale(6, 3, "1.00"))


async def test_a_transaction_takes_in_no_other_write(shop):
    started, written = asyncio.Event(), asyncio.Event()

    async def discarded():
        with pytest.raises(ZeroDivisionError):
            async with shop.transaction() as tx:
                await tx.insert(sale(4, 1, "1.00"))
                started.set()
                await written.wait()
                raise ZeroDivisionError

    async def kept():
        await started.wait()
        await shop.insert(sale(5, 1, "1.00"))
        written.set()

    await asyncio.gather(discarded(), kept())
    assert sorted(s.id for s in await shop.all(Sale)) == [1, 2, 3, 5]


# -- schemas -------------------------------------------------------------------


async def test_a_relation_is_in_the_schema_it_declares(db, client):
    schema = f"{client.schema}x"

    class Account(Table):
        __schema__ = schema
        id: Annotated[int, PrimaryKey]
        customer_id: int
        owner_id: int | None
        customer = link(Customer, via="customer_id")
        owner = link(lambda: Account, via="owner_id")

    class Entry(Table):
        id: Annotated[int, PrimaryKey]
        account_id: int
        amount: int
        account = link(Account, via="account_id")

    class Balance(View[Entry, Account]):
        __schema__ = schema
        account_id: int
        customer: str = Account.customer.name
        owner: int | None = Account.owner.customer_id
        balance: int = sum_(Entry.amount)

    try:
        await db.create(Customer, Account, Entry, Balance)
        await db.create(Account, Balance)
        await db.insert(
            ANN,
            Account(id=7, customer_id=1, owner_id=None),
            Account(id=8, customer_id=1, owner_id=7),
            Entry(id=1, account_id=8, amount=5),
            Entry(id=2, account_id=8, amount=6),
        )
        assert await db.all(Account, where=Account.owner_id == 7) == [
            Account(id=8, customer_id=1, owner_id=7)
        ]
        assert await db.all(Balance, where=Balance.balance > 10) == [
            Balance(account_id=8, customer="ann", owner=1, balance=11)
        ]
        assert client.resolve_table(f"{schema}.balance")
        with pytest.raises(gnitz.GnitzNotFoundError):
            client.resolve_table("balance")
    finally:
        # What reads the schema's relations from outside it goes first.
        client.execute_sql(f"DROP VIEW IF EXISTS {schema}.balance; DROP TABLE IF EXISTS entry")
        client.drop_schema(schema)


# -- changes -------------------------------------------------------------------


async def test_changes_are_the_views_value_and_then_what_changed(shop):
    changes = shop.changes(Spend, every=0.001)
    first = await anext(changes)
    assert first.reset and first.removed == []
    assert sorted(s.customer_id for s in first.added) == [1, 2]

    await shop.insert(sale(4, 2, "100.00"))
    delta = await asyncio.wait_for(anext(changes), 5)
    assert not delta.reset
    assert delta.removed == [Spend(customer_id=2, orders=1, spent=Decimal("400.00"))]
    assert delta.added == [Spend(customer_id=2, orders=2, spent=Decimal("500.00"))]
    await changes.aclose()


async def test_changes_start_over_when_the_view_is_created_anew(shop):
    changes = shop.changes(Spend, every=0.001)
    await anext(changes)
    await shop.drop(Spend)
    await shop.delete(Sale, 1, 2)
    await shop.create(Spend)
    again = await asyncio.wait_for(anext(changes), 5)
    assert again.reset
    assert again.added == [Spend(customer_id=2, orders=1, spent=Decimal("400.00"))]
    await changes.aclose()


async def test_only_a_view_with_a_delta_has_changes(shop):
    class Quiet(View[Sale]):
        id: int

    with refused("is not a view that declares __delta__"):
        await anext(shop.changes(Quiet, every=1))
    with refused("is not a view that declares __delta__"):
        await anext(shop.changes(Sale, every=1))
