"""SQL expressions, typed as the values they compute.

A column read off a class (`Sale.total`) and every function here is annotated
with the Python type of its *value*, not with an expression type. That is what
lets a view field's definition be checked against its annotation::

    spent: Decimal = sum_(Sale.total)

At runtime each of them is an `Expr`.
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, NamedTuple
from uuid import UUID

type Kind = Literal["row", "aggregate", "window"]

# The clause a predicate over an expression belongs to follows from its kind:
# WHERE, HAVING, QUALIFY. A compound expression has the last kind of its parts.
KINDS: tuple[Kind, ...] = ("row", "aggregate", "window")


class Join(NamedTuple):
    """A join that reading through a link requires."""

    parent: str  # the alias the link is read off
    link: str
    sql: str  # what follows the JOIN keyword
    nullable: bool  # the link is, so its target is missing wherever it is NULL


class Expr:
    __slots__ = ("kind", "reads", "sql")

    kind: Kind

    def __init__(self, template: str, *args: object, kind: Kind = "row"):
        """`template`, each `{}` filled with an expression or a literal."""
        exprs = [a for a in args if isinstance(a, Expr)]
        self.sql = template.format(*map(render, args))
        kinds: list[Kind] = [kind, *(p.kind for p in exprs)]
        self.kind = max(kinds, key=KINDS.index)
        # The aliases it reads: a source of the view, or what a join brings in.
        self.reads: dict[str, Join | None] = {a: j for p in exprs for a, j in p.reads.items()}

    def _binary(self, op: str, other: object) -> Expr:
        return Expr(f"({{}} {op} {{}})", self, other)

    def _reflected(self, op: str, other: object) -> Expr:
        return Expr(f"({{}} {op} {{}})", other, self)

    def __eq__(self, other: object) -> Expr:  # type: ignore[override]
        return self._binary("=", other)

    def __ne__(self, other: object) -> Expr:  # type: ignore[override]
        return self._binary("<>", other)

    def __lt__(self, other: object) -> Expr:
        return self._binary("<", other)

    def __le__(self, other: object) -> Expr:
        return self._binary("<=", other)

    def __gt__(self, other: object) -> Expr:
        return self._binary(">", other)

    def __ge__(self, other: object) -> Expr:
        return self._binary(">=", other)

    def __add__(self, other: object) -> Expr:
        return self._binary("+", other)

    def __radd__(self, other: object) -> Expr:
        return self._reflected("+", other)

    def __sub__(self, other: object) -> Expr:
        return self._binary("-", other)

    def __rsub__(self, other: object) -> Expr:
        return self._reflected("-", other)

    def __mul__(self, other: object) -> Expr:
        return self._binary("*", other)

    def __rmul__(self, other: object) -> Expr:
        return self._reflected("*", other)

    def __truediv__(self, other: object) -> Expr:
        return self._binary("/", other)

    def __rtruediv__(self, other: object) -> Expr:
        return self._reflected("/", other)

    def __and__(self, other: object) -> Expr:
        return self._binary("AND", other)

    def __or__(self, other: object) -> Expr:
        return self._binary("OR", other)

    def __invert__(self) -> Expr:
        return Expr("(NOT {})", self)

    def __neg__(self) -> Expr:
        return Expr("(-{})", self)

    def __bool__(self) -> bool:
        raise TypeError(f"{self.sql} is a SQL expression; combine conditions with & | ~")

    def __repr__(self) -> str:
        return f"<{self.kind} expression {self.sql}>"


class Column(Expr):
    """A column of one source of a view, or of what a path from one reaches."""

    __slots__ = ("alias", "base", "nullable")

    def __init__(
        self, alias: str, name: str, base: type, nullable: bool, reads: dict[str, Join | None]
    ):
        super().__init__(f"{alias}.{name}")
        self.alias, self.base, self.nullable, self.reads = alias, base, nullable, reads


def render(value: object) -> str:
    """`value` as SQL: an expression's text, or a literal."""
    if isinstance(value, Expr):
        return value.sql
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float | Decimal):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, UUID):
        return f"'{value}'"
    raise TypeError(f"{value!r} has no SQL literal")


def _expr(value: object) -> Expr:
    if not isinstance(value, Expr):
        raise TypeError(f"expected a column or an expression over one, got {value!r}")
    return value


def sql(template: str, *args: object, kind: Kind = "row") -> Any:
    """A SQL fragment this module has no function for, each `{}` filled with an
    expression or a literal::

        bucket: str = sql("CASE WHEN {} > 100 THEN 'big' ELSE 'small' END", Sale.total)

    `kind` says what the fragment is where its arguments do not:
    `sql("AVG({})", Sale.total, kind="aggregate")`.
    """
    return Expr(template, *args, kind=kind)


_STAR: Any = object()


def count(expr: object = _STAR) -> int:
    """`COUNT(*)`, or `COUNT(expr)`: the rows where `expr` is not NULL."""
    if expr is _STAR:
        return sql("COUNT(*)", kind="aggregate")
    return sql("COUNT({})", _expr(expr), kind="aggregate")


def count_distinct(expr: object) -> int:
    return sql("COUNT(DISTINCT {})", _expr(expr), kind="aggregate")


def sum_[T](expr: T) -> T:
    return sql("SUM({})", _expr(expr), kind="aggregate")


def min_[T](expr: T) -> T:
    return sql("MIN({})", _expr(expr), kind="aggregate")


def max_[T](expr: T) -> T:
    return sql("MAX({})", _expr(expr), kind="aggregate")


def coalesce[T](expr: T | None, default: T) -> T:
    return sql("COALESCE({}, {})", _expr(expr), default)


def is_null(expr: object) -> bool:
    return sql("({} IS NULL)", _expr(expr))


def desc[T](expr: T) -> T:
    """Descending, in a window's `order_by`."""
    return sql("{} DESC", _expr(expr))


def row_number(*, partition_by: Sequence[object] = (), order_by: Sequence[object] = ()) -> int:
    over = [
        f"{clause} {', '.join('{}' for _ in exprs)}"
        for clause, exprs in (("PARTITION BY", partition_by), ("ORDER BY", order_by))
        if exprs
    ]
    exprs = (_expr(e) for e in (*partition_by, *order_by))
    return sql(f"ROW_NUMBER() OVER ({' '.join(over)})", *exprs, kind="window")
