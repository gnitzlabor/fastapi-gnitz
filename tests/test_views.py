"""A view generated from its class holds the rows of the SQL it stands for.

Each case declares views and gives the statement a person would have written
for the last of them. Both are created on a real server and compared as bags —
rows with their weights — so a join that doubled a row fails, not only one that
lost it.
"""

import collections
from decimal import Decimal
from typing import Annotated, Literal

import pytest
from _shop import Customer, Employee, Peer, Refund, Sale, Transfer, load
from annotated_types import Ge, Gt, Le, Lt

from fastapi_gnitz import (
    Cross,
    Exists,
    NotExists,
    NotNull,
    View,
    coalesce,
    count,
    count_distinct,
    ddl,
    desc,
    is_null,
    max_,
    row_number,
    sql,
    sum_,
)

CASES = []


def case(written):
    def register(declare):
        CASES.append(pytest.param(declare, written, id=declare.__name__))
        return declare

    return register


# -- filters -------------------------------------------------------------------


@case("SELECT id, status, total FROM sale WHERE status = 'open' AND total > 100")
def literal_and_bound():
    class BigOpenSale(View[Sale]):
        id: int
        status: Literal["open"]
        total: Annotated[Decimal, Gt(100)]

    return BigOpenSale


@case("SELECT id, status, qty FROM sale WHERE status IN ('open', 'paid') AND qty >= 2 AND qty < 10")
def literals_and_range():
    class MidSale(View[Sale]):
        id: int
        status: Literal["open", "paid"]
        qty: Annotated[int, Ge(2), Lt(10)]

    return MidSale


@case("SELECT id, tier FROM customer WHERE tier IS NOT NULL")
def not_null_marker():
    class Tiered(View[Customer]):
        id: int
        tier: Annotated[int, NotNull]

    return Tiered


@case("SELECT id, tier FROM customer WHERE tier IS NULL OR tier > 1")
def where_takes_any_condition():
    class Untiered(View[Customer]):
        # A type checker sees the column as its value, so it objects to ordering an optional one.
        __where__ = is_null(Customer.tier) | (Customer.tier > 1)  # pyright: ignore[reportOptionalOperand]
        id: int
        tier: int | None

    return Untiered


@case("SELECT id FROM sale WHERE status = 'void'")
def where_on_a_column_the_view_drops():
    class Voided(View[Sale]):
        __where__ = Sale.status == "void"
        id: int

    return Voided


# -- expressions ---------------------------------------------------------------


@case("SELECT id, customer_id AS cust, total * qty AS amount, 2 * qty AS pairs FROM sale")
def renamed_and_computed():
    class SaleAmount(View[Sale]):
        id: int
        cust: int = Sale.customer_id
        amount: Decimal = Sale.total * Sale.qty
        pairs: int = 2 * Sale.qty

    return SaleAmount


@case("SELECT id, COALESCE(tier, 0) AS tier FROM customer")
def coalesced():
    class CustomerTier(View[Customer]):
        id: int
        tier: int = coalesce(Customer.tier, 0)

    return CustomerTier


@case("SELECT id, CASE WHEN total > 100 THEN 'big' ELSE 'small' END AS bucket FROM sale")
def raw_fragment():
    class Bucketed(View[Sale]):
        id: int
        bucket: str = sql("CASE WHEN {} > {} THEN 'big' ELSE 'small' END", Sale.total, 100)

    return Bucketed


# -- aggregates ----------------------------------------------------------------


@case(
    "SELECT customer_id, COUNT(*) AS orders, SUM(total) AS spent, MAX(total) AS biggest "
    "FROM sale GROUP BY customer_id"
)
def group_by_is_inferred():
    class CustomerStats(View[Sale]):
        customer_id: int
        orders: int = count()
        spent: Decimal = sum_(Sale.total)
        biggest: Decimal = max_(Sale.total)

    return CustomerStats


@case("SELECT customer_id, COUNT(*) AS orders FROM sale GROUP BY customer_id HAVING COUNT(*) > 1")
def bound_on_an_aggregate_is_having():
    class Repeat(View[Sale]):
        customer_id: int
        orders: Annotated[int, Gt(1)] = count()

    return Repeat


@case("SELECT COUNT(*) AS n, SUM(total) AS revenue FROM sale")
def global_aggregate():
    class Totals(View[Sale]):
        n: int = count()
        revenue: Decimal | None = sum_(Sale.total)

    return Totals


@case("SELECT status, COUNT(DISTINCT customer_id) AS customers FROM sale GROUP BY status")
def distinct_count():
    class Reach(View[Sale]):
        status: str
        customers: int = count_distinct(Sale.customer_id)

    return Reach


@case(
    "SELECT customer_id, status, SUM(total) AS spent FROM sale WHERE status = 'paid' "
    "GROUP BY customer_id, status"
)
def filter_then_group():
    class PaidSpend(View[Sale]):
        customer_id: int
        status: Literal["paid"]
        spent: Decimal = sum_(Sale.total)

    return PaidSpend


# -- links ---------------------------------------------------------------------


@case(
    "SELECT s.id AS id, c.name AS customer, s.total AS total FROM sale s "
    "JOIN customer c ON s.customer_id = c.id"
)
def lookup_through_a_link():
    class SaleLine(View[Sale]):
        id: int
        customer: str = Sale.customer.name
        total: Decimal

    return SaleLine


@case(
    "SELECT e.id AS eid, m.name AS boss FROM employee e LEFT JOIN employee m ON e.manager_id = m.id"
)
def nullable_link_is_a_left_join():
    class Boss(View[Employee]):
        eid: int = Employee.id
        boss: str | None = Employee.manager.name

    return Boss


@case(
    "SELECT e.id AS eid, m.id AS mid FROM employee e JOIN employee m ON e.manager_id = m.id "
    "WHERE e.sal > m.sal"
)
def condition_across_a_link():
    class Richer(View[Employee]):
        __where__ = Employee.sal > Employee.manager.sal
        eid: int = Employee.id
        mid: Annotated[int, NotNull] = Employee.manager.id

    return Richer


@case(
    "SELECT t.id AS id, a.name AS sender, b.name AS receiver FROM transfer t "
    "JOIN customer a ON t.sender_id = a.id JOIN customer b ON t.receiver_id = b.id"
)
def two_links_to_one_table():
    class TransferLine(View[Transfer]):
        id: int
        sender: str = Transfer.sender.name
        receiver: str = Transfer.receiver.name

    return TransferLine


@case(
    "SELECT r.id AS id, c.name AS customer FROM refund r JOIN sale s ON r.sale_id = s.id "
    "JOIN customer c ON s.customer_id = c.id"
)
def two_hops():
    class RefundLine(View[Refund]):
        id: int
        customer: str = Refund.sale.customer.name

    return RefundLine


@case(
    "SELECT e.id AS id, g.name AS grandboss FROM employee e "
    "LEFT JOIN employee m ON e.manager_id = m.id LEFT JOIN employee g ON m.manager_id = g.id"
)
def two_hops_through_one_link():
    class Lineage(View[Employee]):
        id: int
        grandboss: str | None = Employee.manager.manager.name

    return Lineage


@case(
    "SELECT c.country AS country, COUNT(*) AS n, SUM(s.total) AS revenue FROM sale s "
    "JOIN customer c ON s.customer_id = c.id GROUP BY c.country"
)
def group_by_a_linked_column():
    class CountryRevenue(View[Sale]):
        country: str = Sale.customer.country
        n: int = count()
        revenue: Decimal = sum_(Sale.total)

    return CountryRevenue


# -- sources -------------------------------------------------------------------


@case(
    "SELECT s.id AS id, c.name AS customer, s.total AS total FROM sale s "
    "JOIN customer c ON s.customer_id = c.id"
)
def join_along_the_link():
    class SaleLine(View[Sale, Customer]):
        id: int = Sale.id
        customer: str = Customer.name
        total: Decimal

    return SaleLine


@case(
    "SELECT c.name AS name, s.id AS sale_id FROM customer c "
    "LEFT JOIN sale s ON s.customer_id = c.id"
)
def optional_source_is_a_left_join():
    class CustomerSale(View[Customer, Sale | None]):
        name: str
        sale_id: int | None = Sale.id

    return CustomerSale


@case(
    "SELECT c.name AS name, s.id AS sale_id FROM sale s "
    "RIGHT JOIN customer c ON s.customer_id = c.id"
)
def optional_first_source_is_a_right_join():
    class CustomerSale(View[Sale | None, Customer]):
        name: str
        sale_id: int | None = Sale.id

    return CustomerSale


@case(
    "SELECT r.id AS id, s.id AS sale_id, c.name AS customer FROM refund r "
    "RIGHT JOIN sale s ON r.sale_id = s.id JOIN customer c ON s.customer_id = c.id"
)
def only_the_join_to_an_optional_first_source_is_turned():
    class SaleRefund(View[Refund | None, Sale, Customer]):
        id: int | None = Refund.id
        sale_id: int = Sale.id
        customer: str = Customer.name

    return SaleRefund


@case(
    "SELECT id, status FROM sale WHERE status = 'open' AND id > 1 "
    "UNION ALL SELECT id, status FROM sale WHERE status = 'paid' AND id > 1"
)
def union_filters_each_member():
    class OpenSale(View[Sale]):
        id: int
        status: Literal["open"]

    class PaidSale(View[Sale]):
        id: int
        status: Literal["paid"]

    class LaterSale(View[OpenSale | PaidSale]):
        id: Annotated[int, Gt(1)]
        status: str

    return OpenSale, PaidSale, LaterSale


@case(
    "SELECT r.id AS id, r.amount AS amount, c.name AS customer FROM refund r "
    "JOIN sale s ON r.sale_id = s.id JOIN customer c ON s.customer_id = c.id"
)
def three_sources():
    class RefundLine(View[Refund, Sale, Customer]):
        id: int = Refund.id
        amount: Decimal
        customer: str = Customer.name

    return RefundLine


@case("SELECT c.id AS cid, e.id AS eid FROM customer c JOIN employee e ON c.name = e.name")
def unlinked_sources_related_by_where():
    class Staff(View[Customer, Cross[Employee]]):
        __where__ = Customer.name == Employee.name
        cid: int = Customer.id
        eid: int = Employee.id

    return Staff


@case("SELECT e.id AS lo, p.id AS hi FROM employee e, employee p WHERE e.sal < p.sal")
def alias_pairs_a_table_with_itself():
    class PayGap(View[Employee, Cross[Peer]]):
        __where__ = Employee.sal < Peer.sal
        lo: int = Employee.id
        hi: int = Peer.id

    return PayGap


@case("SELECT c.id AS cid, e.id AS eid FROM customer c CROSS JOIN employee e")
def cross_join():
    class Pairs(View[Customer, Cross[Employee]]):
        cid: int = Customer.id
        eid: int = Employee.id

    return Pairs


@case(
    "SELECT id, name FROM customer "
    "WHERE EXISTS (SELECT 1 FROM sale WHERE sale.customer_id = customer.id)"
)
def exists():
    class Buyer(View[Customer, Exists[Sale]]):
        id: int
        name: str

    return Buyer


@case(
    "SELECT id, name FROM customer "
    "WHERE NOT EXISTS (SELECT 1 FROM sale WHERE sale.customer_id = customer.id)"
)
def not_exists():
    class Lurker(View[Customer, NotExists[Sale]]):
        id: int
        name: str

    return Lurker


# -- composition ---------------------------------------------------------------


@case(
    "SELECT customer_id, spent FROM "
    "(SELECT customer_id, SUM(total) AS spent FROM sale GROUP BY customer_id) x WHERE spent > 200"
)
def view_over_a_view():
    class Spend(View[Sale]):
        customer_id: int
        spent: Decimal = sum_(Sale.total)

    class BigSpender(View[Spend]):
        customer_id: int
        spent: Annotated[Decimal, Gt(200)]

    return Spend, BigSpender


@case("SELECT status, COUNT(*) AS n FROM sale WHERE qty >= 2 GROUP BY status")
def inline_view_is_a_cte():
    class Bulk(View[Sale]):
        __inline__ = True
        status: str
        qty: Annotated[int, Ge(2)]

    class BulkByStatus(View[Bulk]):
        status: str
        n: int = count()

    return BulkByStatus


@case(
    "SELECT id, total FROM sale WHERE status = 'open' "
    "UNION ALL SELECT id, total FROM sale WHERE status = 'paid'"
)
def union():
    class OpenSale(View[Sale]):
        __where__ = Sale.status == "open"
        id: int
        total: Decimal

    class PaidSale(View[Sale]):
        __where__ = Sale.status == "paid"
        id: int
        total: Decimal

    class LiveSale(View[OpenSale | PaidSale]):
        id: int
        total: Decimal

    return OpenSale, PaidSale, LiveSale


@case(
    "SELECT customer_id, id FROM sale "
    "QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY total DESC) <= 1"
)
def top_n_by_where():
    class TopSale(View[Sale]):
        __where__ = row_number(partition_by=[Sale.customer_id], order_by=[desc(Sale.total)]) <= 1
        customer_id: int
        id: int

    return TopSale


@case(
    "SELECT customer_id, id, "
    "ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY total DESC) AS rn FROM sale "
    "QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY total DESC) <= 1"
)
def bound_on_a_window_is_qualify():
    class RankedSale(View[Sale]):
        customer_id: int
        id: int
        rn: Annotated[int, Le(1)] = row_number(
            partition_by=[Sale.customer_id], order_by=[desc(Sale.total)]
        )

    return RankedSale


@case("SELECT customer_id, COUNT(*) AS orders FROM sale GROUP BY customer_id")
def written_by_hand():
    class Orders(View):
        __sql__ = "SELECT customer_id, COUNT(*) AS orders FROM sale GROUP BY customer_id"
        customer_id: int
        orders: int

    return Orders


def bag(client, relation):
    rows = collections.Counter()
    for row in client.scan(*client.resolve_table(relation)):
        rows[tuple(row)] += row._weight
    return +rows


@pytest.mark.parametrize(("declare", "written"), CASES)
def test_generated_view_holds_the_rows_of_the_written_one(client, declare, written):
    load(client)
    declared = declare()
    views = declared if isinstance(declared, tuple) else (declared,)
    for view in views:
        client.execute_sql(ddl(view))
    client.execute_sql(f"CREATE VIEW written AS {written}")

    generated = bag(client, views[-1].__relation__)
    assert generated, "the case selects nothing, so it compares nothing"
    assert generated == bag(client, "written")

    _, schema = client.resolve_table(views[-1].__relation__)
    visible = [column.name for column in schema.columns if not column.is_hidden]
    assert visible == list(views[-1].model_fields)
