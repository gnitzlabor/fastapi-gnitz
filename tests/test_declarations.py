"""What a declaration generates, and what it refuses when the class is defined."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

import gnitz
import pytest
from _shop import Customer, Employee, Money, Peer, Sale, Transfer, load, refused
from annotated_types import Len, MultipleOf
from pydantic import ValidationError

from fastapi_gnitz import Exists, PrimaryKey, Table, View, count, ddl, link, sum_

# -- tables --------------------------------------------------------------------


def test_table_ddl_carries_keys_types_and_foreign_keys():
    assert ddl(Sale) == (
        "CREATE TABLE sale (id BIGINT NOT NULL, customer_id BIGINT NOT NULL, status TEXT NOT NULL, "
        "total DECIMAL(12, 2) NOT NULL, qty BIGINT NOT NULL, PRIMARY KEY (id), "
        "FOREIGN KEY (customer_id) REFERENCES customer(id))"
    )
    assert "manager_id BIGINT, " in ddl(Employee)

    class Tip(Table):
        id: Annotated[int, PrimaryKey]
        amount: Money | None

    assert "amount DECIMAL(12, 2), " in ddl(Tip)
    assert "FOREIGN KEY (manager_id) REFERENCES employee(id)" in ddl(Employee)


def test_every_column_type_is_one_the_server_takes(client):
    class Everything(Table):
        a: Annotated[int, PrimaryKey]
        b: Annotated[int, PrimaryKey]
        f: float
        s: str | None
        day: date
        at: datetime
        ident: uuid.UUID

    client.execute_sql(ddl(Everything))
    _, schema = client.resolve_table("everything")
    assert [schema.columns[i].name for i in schema.pk_indices] == ["a", "b"]
    assert [c.name for c in schema.columns if c.is_nullable] == ["s"]


def test_relation_name_can_be_set():
    class Order(Table):
        __relation__ = "orders"
        id: Annotated[int, PrimaryKey]

    class OrderIds(View[Order]):
        id: int

    assert ddl(Order).startswith("CREATE TABLE orders (")
    assert ddl(OrderIds) == "CREATE VIEW order_ids AS SELECT orders.id AS id FROM orders"


def test_schema_and_delta_are_part_of_the_statement():
    class Ledger(Table):
        __schema__ = "books"
        id: Annotated[int, PrimaryKey]
        sale_id: int
        parent_id: int | None
        sale = link(Sale, via="sale_id")
        parent = link(lambda: Ledger, via="parent_id")

    class Posted(View[Ledger]):
        __schema__ = "books"
        __delta__ = "64MB"
        id: int
        parent_sale: int | None = Ledger.parent.sale_id

    assert ddl(Ledger, if_not_exists=True) == (
        "CREATE TABLE IF NOT EXISTS books.ledger (id BIGINT NOT NULL, sale_id BIGINT NOT NULL, "
        "parent_id BIGINT, PRIMARY KEY (id), FOREIGN KEY (sale_id) REFERENCES sale(id), "
        "FOREIGN KEY (parent_id) REFERENCES books.ledger(id))"
    )
    assert ddl(Posted) == (
        "CREATE VIEW books.posted WITH (delta = '64MB') AS "
        "SELECT ledger.id AS id, ledger__parent.sale_id AS parent_sale FROM books.ledger ledger "
        "LEFT JOIN books.ledger ledger__parent ON ledger.parent_id = ledger__parent.id"
    )
    assert ddl(Posted, if_not_exists=True).startswith("CREATE VIEW IF NOT EXISTS books.posted ")

    with refused("'Not A Schema' is not a usable schema name"):

        class Bad(Table):
            __schema__ = "Not A Schema"
            id: Annotated[int, PrimaryKey]

    with refused("an inline view is no relation"):

        class Fragment(View[Sale]):
            __inline__ = True
            __schema__ = "x"
            id: int


def test_the_server_enforces_a_link(client):
    load(client)
    with pytest.raises(gnitz.GnitzError, match="Foreign Key violation"):
        client.execute_sql("INSERT INTO sale VALUES (99, 777, 'open', 1.00, 1)")
    with pytest.raises(gnitz.GnitzError, match="Foreign Key violation"):
        client.execute_sql("INSERT INTO employee VALUES (99, 'x', 777, 1)")
    with pytest.raises(gnitz.GnitzError, match="still referenced"):
        client.execute_sql("DELETE FROM customer WHERE id = 1")


def test_a_relation_is_still_a_model_of_its_rows():
    class Stats(View[Sale]):
        customer_id: int
        orders: int = count()

    sale = Sale(id=1, customer_id=2, status="open", total=Decimal("3.50"), qty=1)
    assert (sale.customer_id, sale.total) == (2, Decimal("3.50"))
    assert Stats(customer_id=1, orders=2).orders == 2
    # A definition is not a default, and a column on the class is not one either.
    assert Stats.model_json_schema()["required"] == ["customer_id", "orders"]
    with pytest.raises(ValidationError):
        Stats.model_validate({"customer_id": 1})
    with pytest.raises(ValidationError):
        Peer.model_validate({"id": 1})


def test_a_table_that_adds_columns_keeps_the_inherited_ones():
    class Vip(Customer):
        perks: str

    assert ddl(Vip) == (
        "CREATE TABLE vip (id BIGINT NOT NULL, name TEXT NOT NULL, country TEXT NOT NULL, "
        "tier BIGINT, perks TEXT NOT NULL, PRIMARY KEY (id))"
    )
    with pytest.raises(ValidationError):
        Vip.model_validate({"perks": "x"})


def test_a_row_holds_no_related_rows():
    employee = Employee(id=1, name="x", manager_id=None, sal=1)
    with pytest.raises(AttributeError, match="a row holds no related rows"):
        _ = employee.manager


def test_a_condition_is_not_a_truth_value():
    with pytest.raises(TypeError, match="combine conditions with"):
        _ = Sale.qty > 1 and Sale.qty < 5


# -- refusals ------------------------------------------------------------------


def test_link_must_name_a_column():
    with refused("via='customer_id' names no column"):

        class Bad(Table):
            id: Annotated[int, PrimaryKey]
            cust: int
            customer = link(Customer, via="customer_id")


def test_link_column_must_have_the_type_of_the_key_it_references():
    class Mistyped(Table):
        id: Annotated[int, PrimaryKey]
        cust: str
        customer = link(Customer, via="cust")

    with refused("cust is str, Customer.id is int"):
        ddl(Mistyped)


def test_table_needs_a_key_and_decimal_a_scale():
    class Keyless(Table):
        name: str

    class Unscaled(Table):
        id: Annotated[int, PrimaryKey]
        amount: Decimal

    with refused("has no primary key"):
        ddl(Keyless)
    with refused("needs Field"):
        ddl(Unscaled)


def test_field_type_must_match_the_source():
    with refused("declared int, the source column is Decimal"):

        class Bad(View[Sale]):
            total: int


def test_nullable_source_must_be_declared_or_filtered():
    with refused("the source is nullable"):

        class Narrowed(View[Customer]):
            tier: int

    with refused("the source is nullable"):

        class ThroughNullableLink(View[Employee]):
            boss: str = Employee.manager.name

    with refused("the source is nullable"):

        class FromOptionalSource(View[Customer, Sale | None]):
            sale_id: int = Sale.id


def test_a_path_off_an_optional_source_is_optional_too():
    class Note(Table):
        id: Annotated[int, PrimaryKey]
        sale_id: int
        author_id: int
        sale = link(Sale, via="sale_id")
        author = link(Employee, via="author_id")

    class Noted(View[Sale, Note | None]):
        id: int = Sale.id
        author: str | None = Note.author.name

    assert "LEFT JOIN employee note__author ON note.author_id = note__author.id" in ddl(Noted)
    with refused("the source is nullable"):

        class Narrowed(View[Sale, Note | None]):
            id: int = Sale.id
            author: str = Note.author.name


def test_bare_field_must_be_one_source_column():
    with refused("2 sources have this column"):

        class Ambiguous(View[Sale, Customer]):
            id: int

    with refused("0 sources have this column"):

        class Missing(View[Sale]):
            nope: int


def test_sources_must_be_related():
    with refused("0 links relate Employee to the other sources"):

        class Unrelated(View[Customer, Employee]):
            cid: int = Customer.id

    with refused("2 links relate Customer"):

        class TwoLinks(View[Transfer, Customer]):
            id: int = Transfer.id

    with refused("0 links relate Employee"):

        class NoLink(View[Customer, Exists[Employee]]):
            id: int


def test_what_is_read_must_be_a_source():
    with refused("customer is read but is not a source"):

        class Stray(View[Sale]):
            id: int
            name: str = Customer.name


def test_a_link_is_joined_one_way():
    with refused("read as a path, and its target is also a source"):

        class Twice(View[Sale, Customer]):
            id: int = Sale.id
            a: str = Customer.name
            b: str = Sale.customer.name


def test_a_constraint_with_no_filter_is_refused():
    with refused("is no filter this can generate"):

        class Even(View[Sale]):
            qty: Annotated[int, MultipleOf(2)]

    with refused("is no filter this can generate"):

        class Short(View[Customer]):
            name: Annotated[str, Len(1, 3)]


def test_a_view_defines_only_the_inline_views_it_reads():
    class Paid(View[Sale]):
        __inline__ = True
        id: int
        status: Literal["paid"]

    class PaidIds(View[Paid]):
        id: int

    class Count(View[PaidIds]):
        n: int = count()

    assert PaidIds.__sql__.startswith("WITH paid AS (")
    assert Count.__sql__ == "SELECT COUNT(*) AS n FROM paid_ids"


def test_union_and_handwritten_views_declare_columns_only():
    class Paid(View[Sale]):
        id: int
        status: Literal["paid"]

    class Open(View[Sale]):
        id: int
        status: Literal["open"]

    with refused("a union takes its members' columns as they are"):

        class Bad(View[Paid | Open]):
            n: int = count()

    with refused("declared str, the source column is int"):

        class Mismatched(View[Paid | Open]):
            status: Literal["paid"] | None
            id: str

    with refused("a union is a view's only source"):

        class Both(View[Paid | Open, Customer]):
            id: int = Paid.id

    with refused("declares only its columns"):

        class AlsoBad(View):
            __sql__ = "SELECT 1 AS n"
            n: int = count()

    with refused("a view reads something"):

        class Sourceless(View):
            n: int


def test_inline_view_and_alias_are_no_relations():
    class Fragment(View[Sale]):
        __inline__ = True
        id: int

    with refused("is inline"):
        ddl(Fragment)
    with refused("is an alias of Employee"):
        ddl(Peer)


def test_a_view_is_not_extended():
    class Spend(View[Sale]):
        customer_id: int
        spent: Decimal = sum_(Sale.total)

    with refused("a view is not extended"):

        class More(Spend):
            extra: int
