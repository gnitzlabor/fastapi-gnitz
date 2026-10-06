"""The tables the tests read, a view of them, and the rows in them."""

from decimal import Decimal
from typing import Annotated

import pytest
from pydantic import Field

from fastapi_gnitz import PrimaryKey, Table, View, count, ddl, link, sum_

Money = Annotated[Decimal, Field(max_digits=12, decimal_places=2)]


class Customer(Table):
    id: Annotated[int, PrimaryKey]
    name: str
    country: str
    tier: int | None


class Sale(Table):
    id: Annotated[int, PrimaryKey]
    customer_id: int
    status: str
    total: Money
    qty: int
    customer = link(Customer, via="customer_id")


class Refund(Table):
    id: Annotated[int, PrimaryKey]
    sale_id: int
    amount: Money
    sale = link(Sale, via="sale_id")


class Transfer(Table):
    id: Annotated[int, PrimaryKey]
    sender_id: int
    receiver_id: int
    amount: Money
    sender = link(Customer, via="sender_id")
    receiver = link(Customer, via="receiver_id")


class Employee(Table):
    id: Annotated[int, PrimaryKey]
    name: str
    manager_id: int | None
    sal: int
    manager = link(lambda: Employee, via="manager_id")


class Peer(Employee):
    """`Employee` again, to pair employees no link relates."""


TABLES = (Customer, Sale, Refund, Transfer, Employee)


class Spend(View[Sale]):
    __delta__ = "1MB"
    customer_id: Annotated[int, PrimaryKey]
    orders: int = count()
    spent: Decimal = sum_(Sale.total)


def refused(match):
    return pytest.raises(TypeError, match=match)


ROWS = """
INSERT INTO customer VALUES (1, 'ann', 'DE', 1), (2, 'bob', 'DE', NULL), (3, 'cy', 'FR', 2),
    (4, 'dee', 'FR', NULL);
INSERT INTO sale VALUES (1, 1, 'open', 50.00, 1), (2, 1, 'paid', 150.00, 2),
    (3, 2, 'paid', 400.00, 3), (4, 3, 'open', 120.00, 12), (5, 3, 'void', 10.00, 2),
    (6, 3, 'paid', 90.50, 4);
INSERT INTO refund VALUES (1, 2, 20.00), (2, 3, 500.00), (3, 6, 90.50);
INSERT INTO transfer VALUES (1, 1, 2, 5.00), (2, 2, 3, 7.00), (3, 3, 1, 9.00), (4, 1, 1, 1.00);
INSERT INTO employee VALUES (1, 'root', NULL, 100), (2, 'ann', 1, 120), (3, 'b', 1, 80),
    (4, 'c', 2, 90), (5, 'd', 4, 95)
"""


def load(client):
    for table in TABLES:
        client.execute_sql(ddl(table))
    client.execute_sql(ROWS)
