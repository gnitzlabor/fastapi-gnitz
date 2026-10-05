"""Tables, the links between them, and what tables and views share.

A relation is a plain Pydantic model. Read off the *class*, a field is its
column (`Sale.total`), which is what view definitions are written in; read off
an instance it is the row's value, as on any model.
"""

import annotationlib
import re
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from types import NoneType, UnionType
from typing import Any, ClassVar, Literal, NamedTuple, get_args, get_origin, overload
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic.fields import FieldInfo

from fastapi_gnitz._expr import Column, Expr, Join

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*\Z")

# What gnitz's SQL has a type name for. A TIMESTAMP holds no time zone.
_SQL_TYPES: dict[type, str] = {
    int: "BIGINT",
    float: "DOUBLE",
    str: "TEXT",
    date: "DATE",
    datetime: "TIMESTAMP",
    UUID: "UUID",
}


class PrimaryKey:
    """Marks a key column: `id: Annotated[int, PrimaryKey]`."""


class NotNull:
    """On a view field over a nullable source: keep only the rows where it is
    not NULL. Without it, declaring such a field non-optional is an error."""


class Link:
    """What `link()` returns at runtime."""

    name: str

    def __init__(self, target: Any, via: str):
        self._target, self.via = target, via

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    @property
    def target(self) -> type[Relation]:
        target = self._target
        return target if isinstance(target, type) else target()

    @property
    def key(self) -> str:
        """The column of the target that `via` references: its one primary key."""
        target = self.target
        keys = primary_keys(target)
        if len(keys) != 1:
            raise TypeError(
                f"{target.__name__} has {len(keys)} primary key columns; a link needs exactly one"
            )
        return keys[0]

    def on(self, owner: str, target: str) -> str:
        """The join condition, between an alias of the link's owner and one of its target."""
        return f"{owner}.{self.via} = {target}.{self.key}"

    def __get__(self, row: object, owner: type[Relation]) -> Path:
        if row is not None:
            raise AttributeError(
                f"{owner.__name__}.{self.name} is a path for view definitions; "
                "a row holds no related rows"
            )
        return Path(owner, owner.__alias__, self, {owner.__alias__: None})


@overload
def link[T](target: type[T], *, via: str) -> T: ...
@overload
def link[T](target: Callable[[], type[T]], *, via: str) -> T: ...
def link(target: Any, *, via: str) -> Any:
    """A to-one relationship: the column `via` references `target`'s primary key.

    It declares the foreign key, and it is a path in view definitions, joined
    as often as it is read through::

        class Sale(Table):
            customer_id: int
            customer = link(Customer, via="customer_id")

        class SaleLine(View[Sale]):
            customer: str = Sale.customer.name

    A nullable `via` is a LEFT JOIN, so what is read through it is optional. A
    table that references itself names itself lazily: `link(lambda: Employee, ...)`.
    """
    return Link(target, via)


class Path:
    """One hop through a link: the target's columns, under an alias of their own."""

    def __init__(
        self, owner: type[Relation], parent: str, link: Link, reads: dict[str, Join | None]
    ):
        target = link.target
        alias = f"{parent}__{link.name}"
        sql = f"{target.__relation__} {alias} ON {link.on(parent, alias)}"
        join = Join(parent, link.name, sql, owner.__columns__[link.via].nullable)
        self._target, self._alias, self._reads = target, alias, {**reads, alias: join}

    def __getattr__(self, name: str) -> Any:
        target = self._target
        column = target.__columns__.get(name)
        if column is not None:
            return Column(self._alias, name, column.base, column.nullable, self._reads)
        link = target.__links__.get(name)
        if link is not None:
            return Path(target, self._alias, link, self._reads)
        raise AttributeError(f"{target.__name__} has no column or link {name!r}")


class ColumnType(NamedTuple):
    """A field's column type, taken apart."""

    base: type
    nullable: bool
    literals: tuple[Any, ...]
    metadata: list[Any]


def column_type(field: FieldInfo) -> ColumnType:
    annotation: Any = field.annotation
    metadata = list(field.metadata)
    nullable = False
    if isinstance(annotation, UnionType):
        members = [a for a in get_args(annotation) if a is not NoneType]
        if len(members) != 1 or len(members) == len(get_args(annotation)):
            raise TypeError(f"{annotation} is not a column type; only `T | None` is a union")
        # Pydantic took the field's own `Annotated` apart, not one under the `| None`.
        inner = FieldInfo.from_annotation(members[0])
        annotation, nullable = inner.annotation, True
        metadata += inner.metadata
    literals: tuple[Any, ...] = ()
    if get_origin(annotation) is Literal:
        literals = get_args(annotation)
        kinds = {type(v) for v in literals}
        if len(kinds) != 1:
            raise TypeError(f"{annotation} mixes types")
        annotation = kinds.pop()
    return ColumnType(annotation, nullable, literals, metadata)


class ColumnOf:
    """Makes `Sale.total` the field's column. On a row, the field's value shadows it."""

    def __init__(self, name: str):
        self.name = name

    def __get__(self, row: object, owner: type[Relation]) -> Column:
        # A class still being defined has no columns of its own: Pydantic,
        # collecting its fields, must not take an inherited column for a default.
        column = vars(owner).get("__columns__", {}).get(self.name)
        if row is not None or column is None:
            raise AttributeError(self.name)
        alias = owner.__alias__
        return Column(alias, self.name, column.base, column.nullable, {alias: None})


class Relation(BaseModel):
    """What `Table` and `View` share. Not for subclassing directly."""

    model_config = ConfigDict(ignored_types=(Link,))

    __relation__: ClassVar[str]
    """The relation's name in the database: the class name in snake case, unless set."""
    __alias__: ClassVar[str]
    """What a view calls it: `__relation__`, unless the class is an alias."""
    __columns__: ClassVar[dict[str, ColumnType]]
    __links__: ClassVar[dict[str, Link]] = {}
    __definitions__: ClassVar[dict[str, Expr]]
    """The expressions that fields were defined by: `spent: Decimal = sum_(...)`."""

    @classmethod
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Before Pydantic collects the fields, or it would take a definition for a default.
        cls.__definitions__ = {
            name: value
            for name in _annotated(cls)
            if isinstance(value := vars(cls).get(name), Expr)
        }
        for name in cls.__definitions__:
            delattr(cls, name)


def _annotated(cls: type) -> dict[str, Any]:
    return annotationlib.get_annotations(cls, format=annotationlib.Format.FORWARDREF)


def declare(cls: type[Relation]) -> None:
    """Settle `cls`'s name, columns and links, or make it an alias: a subclass
    of a relation that adds no column is that relation under another name, for
    a self-join."""
    if hasattr(cls.__mro__[1], "__relation__") and not _annotated(cls).keys() & cls.model_fields:
        cls.__alias__ = _snake(cls.__name__)
    else:
        cls.__relation__ = cls.__alias__ = vars(cls).get("__relation__") or _snake(cls.__name__)
    for what in (cls.__relation__, cls.__alias__):
        if not _IDENTIFIER.match(what) or "__" in what:
            raise TypeError(f"{cls.__name__}: {what!r} is not a usable relation name")
    cls.__columns__ = {name: column_type(field) for name, field in cls.model_fields.items()}
    for name in cls.__columns__:
        setattr(cls, name, ColumnOf(name))
    cls.__links__ = cls.__links__ | {k: v for k, v in vars(cls).items() if isinstance(v, Link)}
    for link_ in cls.__links__.values():
        if link_.via not in cls.__columns__:
            raise TypeError(f"{cls.__name__}.{link_.name}: via={link_.via!r} names no column")


def is_alias(relation: type[Relation]) -> bool:
    return relation.__alias__ != relation.__relation__


def primary_keys(relation: type[Relation]) -> list[str]:
    return [name for name, column in relation.__columns__.items() if PrimaryKey in column.metadata]


class Table(Relation):
    """A table, declared as the model of its rows::

        class Sale(Table):
            id: Annotated[int, PrimaryKey]
            customer_id: int
            total: Decimal = Field(max_digits=12, decimal_places=2)
            customer = link(Customer, via="customer_id")

    A subclass that adds columns is another table; one that adds none is an
    alias of this one.
    """

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        declare(cls)
        if cls.__definitions__:
            name = next(iter(cls.__definitions__))
            raise TypeError(f"{cls.__name__}.{name}: a table column is not an expression")


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _sql_type(table: type[Relation], name: str, column: ColumnType) -> str:
    if column.base is Decimal:
        digits, places = (
            next((getattr(m, attr) for m in column.metadata if hasattr(m, attr)), None)
            for attr in ("max_digits", "decimal_places")
        )
        if digits is None or places is None:
            raise TypeError(
                f"{table.__name__}.{name}: a Decimal column needs "
                "Field(max_digits=..., decimal_places=...)"
            )
        return f"DECIMAL({digits}, {places})"
    try:
        return _SQL_TYPES[column.base]
    except KeyError:
        raise TypeError(f"{table.__name__}.{name}: {column.base!r} is not a column type") from None


def create_table(table: type[Relation]) -> str:
    keys = primary_keys(table)
    if not keys:
        raise TypeError(f"{table.__name__} has no primary key")
    parts = []
    for name, column in table.__columns__.items():
        null = "" if column.nullable else " NOT NULL"
        parts.append(f"{name} {_sql_type(table, name, column)}{null}")
    parts.append(f"PRIMARY KEY ({', '.join(keys)})")
    for link_ in table.__links__.values():
        target, key = link_.target, link_.key
        mine, theirs = table.__columns__[link_.via].base, target.__columns__[key].base
        if mine is not theirs:
            raise TypeError(
                f"{table.__name__}.{link_.name}: {link_.via} is {mine.__name__}, "
                f"{target.__name__}.{key} is {theirs.__name__}"
            )
        parts.append(f"FOREIGN KEY ({link_.via}) REFERENCES {target.__relation__}({key})")
    return f"CREATE TABLE {table.__relation__} ({', '.join(parts)})"
