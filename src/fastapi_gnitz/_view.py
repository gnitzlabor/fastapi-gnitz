"""Views, declared as the model of their rows, their SQL generated from it."""

from types import GenericAlias, NoneType, UnionType, get_original_bases
from typing import Any, ClassVar, Literal, NamedTuple, get_args, get_origin

from annotated_types import Ge, Gt, Le, Lt, MaxLen, MinLen, MultipleOf, Predicate

from fastapi_gnitz._expr import KINDS, Column, Expr, Kind, render
from fastapi_gnitz._relation import (
    ColumnType,
    NotNull,
    Relation,
    create_table,
    declare,
    is_alias,
)

_COMPARISONS = ((Gt, "gt", ">"), (Ge, "ge", ">="), (Lt, "lt", "<"), (Le, "le", "<="))


class Exists[T]:
    """As a view source: keep the rows that have a `T` linked to them."""


class NotExists[T]:
    """As a view source: keep the rows that have no `T` linked to them."""


class Cross[T]:
    """As a view source: every row paired with every `T`."""


# How a source takes part in the view, as the SQL that says so.
type Mode = Literal["JOIN", "LEFT JOIN", "CROSS JOIN", "EXISTS", "NOT EXISTS", "UNION ALL"]

_MODES: dict[Any, Mode] = {Exists: "EXISTS", NotExists: "NOT EXISTS", Cross: "CROSS JOIN"}


class Source(NamedTuple):
    relation: type[Relation]
    mode: Mode


def _sources(item: Any) -> list[Source]:
    """The sources one item of `View[...]` names: one, or each member of a union."""
    origin = get_origin(item)
    if origin in _MODES:
        return [Source(_relation(get_args(item)[0]), _MODES[origin])]
    if isinstance(item, UnionType):
        members = [a for a in get_args(item) if a is not NoneType]
        if len(members) == len(get_args(item)):
            return [Source(_relation(m), "UNION ALL") for m in members]
        if len(members) != 1:
            raise TypeError(f"{item}: an optional source is one relation `| None`")
        return [Source(_relation(members[0]), "LEFT JOIN")]
    return [Source(_relation(item), "JOIN")]


def _relation(item: Any) -> type[Relation]:
    if not (isinstance(item, type) and issubclass(item, Relation) and hasattr(item, "__alias__")):
        raise TypeError(f"{item!r} is not a table or a view")
    return item


class View[*Sources](Relation):
    """A view, declared as the model of its rows::

        class CustomerStats(View[Sale]):
            customer_id: int
            status: Literal["paid"]
            orders: Annotated[int, Gt(1)] = count()
            spent: Decimal = sum_(Sale.total)

    **Sources.** `View[A]` reads one relation. `View[A, B]` joins `B` along the
    link between them, `View[A, B | None]` as a LEFT JOIN; `Exists[B]` and
    `NotExists[B]` keep the rows of `A` with and without a linked `B`;
    `Cross[B]` pairs every row with every `B`. Two sources no link relates are
    joined by a `__where__` that names both. `View[A | B]` is `A UNION ALL B`.

    **Fields.** A bare field is the source column of that name; a field given a
    value is defined by that expression. Once a field is an aggregate, the
    others are the GROUP BY keys.

    **Filters.** What a field's type says of every row is the view's filter: a
    `Literal`, or a `Gt` / `Ge` / `Lt` / `Le` bound — WHERE on a column, HAVING
    on an aggregate, QUALIFY on a window function. `__where__` takes any other
    condition.

    `__inline__ = True` makes a view a fragment: no relation of its own, a CTE
    in each view that reads it. `__sql__` set by hand is the view's body, for
    what none of this expresses.
    """

    __sources__: ClassVar[tuple[Source, ...]] = ()
    __where__: ClassVar[Any] = None
    __inline__: ClassVar[bool] = False
    __sql__: ClassVar[str]
    __ctes__: ClassVar[dict[str, str]] = {}

    def __class_getitem__(cls, item: Any) -> Any:
        return GenericAlias(cls, item)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        for base in get_original_bases(cls):
            if get_origin(base) is View:
                cls.__sources__ = tuple(s for item in get_args(base) for s in _sources(item))
        declare(cls)
        if is_alias(cls):
            return
        if hasattr(cls.__mro__[1], "__relation__"):
            raise TypeError(f"{cls.__name__}: a view is not extended; read it as a source instead")
        if "__sql__" in vars(cls):
            if cls.__sources__ or cls.__definitions__ or cls.__where__ is not None:
                raise TypeError(f"{cls.__name__}: a view with __sql__ declares only its columns")
            return
        body = _body(cls)
        cls.__ctes__ = {}
        for source in cls.__sources__:
            if issubclass(source.relation, View):
                cls.__ctes__.update(source.relation.__ctes__)
                if source.relation.__inline__:
                    cls.__ctes__[source.relation.__relation__] = source.relation.__sql__
        if cls.__ctes__ and not cls.__inline__:
            with_ = ", ".join(f"{name} AS ({sql})" for name, sql in cls.__ctes__.items())
            body = f"WITH {with_} {body}"
        cls.__sql__ = body


def ddl(relation: type[Relation]) -> str:
    """The statement that creates `relation`."""
    if is_alias(relation):
        raise TypeError(f"{relation.__name__} is an alias of {relation.__mro__[1].__name__}")
    if not issubclass(relation, View):
        return create_table(relation)
    if relation.__inline__:
        raise TypeError(f"{relation.__name__} is inline; it is no relation of its own")
    return f"CREATE VIEW {relation.__relation__} AS {relation.__sql__}"


def _from(relation: type[Relation]) -> str:
    if is_alias(relation):
        return f"{relation.__relation__} {relation.__alias__}"
    return relation.__relation__


def _link_conditions(a: type[Relation], b: type[Relation]) -> list[tuple[str, str, str]]:
    """`(condition, alias, link)` for each link between sources `a` and `b`. An
    alias is related by hand: it exists to be joined some other way."""
    if is_alias(a) or is_alias(b):
        return []
    return [
        (link.on(x.__alias__, y.__alias__), x.__alias__, link.name)
        for x, y in ((a, b), (b, a))
        for link in x.__links__.values()
        if link.target is y
    ]


def _field_predicates(view: str, name: str, expr: Expr, column: ColumnType) -> list[str]:
    found = []
    if len(column.literals) == 1:
        found.append(f"{expr.sql} = {render(column.literals[0])}")
    elif column.literals:
        found.append(f"{expr.sql} IN ({', '.join(render(v) for v in column.literals)})")
    for constraint in column.metadata:
        for kind, attr, op in _COMPARISONS:
            if isinstance(constraint, kind):
                found.append(f"{expr.sql} {op} {render(getattr(constraint, attr))}")
        unfiltered = isinstance(constraint, MultipleOf | MinLen | MaxLen | Predicate)
        if unfiltered or getattr(constraint, "pattern", None) is not None:
            raise TypeError(
                f"{view}.{name}: {constraint!r} is no filter this can generate, "
                "so it would not hold of the view's rows; put the condition in __where__"
            )
    return found


def _body(view: type[View]) -> str:
    name = view.__name__
    sources = view.__sources__
    if not sources:
        raise TypeError(f"{name}: a view reads something: View[Source], or __sql__")
    if all(s.mode != "UNION ALL" for s in sources):
        return _select(view, sources)
    if any(s.mode != "UNION ALL" for s in sources):
        raise TypeError(f"{name}: a union is a view's only source")
    if view.__definitions__ or view.__where__ is not None:
        raise TypeError(
            f"{name}: a union takes its members' columns as they are; filter the members"
        )
    return " UNION ALL ".join(_select(view, (Source(s.relation, "JOIN"),)) for s in sources)


def _select(view: type[View], sources: tuple[Source, ...]) -> str:
    name = view.__name__
    if sources[0].mode not in ("JOIN", "LEFT JOIN"):
        raise TypeError(f"{name}: the first source is the one the others are joined to")
    joined = [s for s in sources if s.mode in ("JOIN", "LEFT JOIN", "CROSS JOIN")]
    # By alias, whether a row can be missing from what the view reads under it.
    missing = {s.relation.__alias__: s.mode == "LEFT JOIN" for s in joined}
    if len(missing) < len(joined):
        raise TypeError(f"{name}: a relation is a source once; join it to itself through an alias")

    exprs: dict[str, Expr] = {}
    for field_name in view.__columns__:
        expr = view.__definitions__.get(field_name)
        if expr is None:
            candidates = [s.relation for s in joined if field_name in s.relation.__columns__]
            if len(candidates) != 1:
                raise TypeError(
                    f"{name}.{field_name}: {len(candidates)} sources have this column; "
                    "define the field"
                )
            expr = getattr(candidates[0], field_name)
        exprs[field_name] = expr
    where = view.__where__
    if where is not None and not isinstance(where, Expr):
        raise TypeError(f"{name}.__where__ is not a condition over columns")

    used = [*exprs.values(), *([] if where is None else [where])]
    reads = {alias: join for expr in used for alias, join in expr.reads.items()}
    stray = sorted(alias for alias, join in reads.items() if not join and alias not in missing)
    if stray:
        raise TypeError(f"{name}: {', '.join(stray)} is read but is not a source of the view")
    # A path is read after what it is read off, so that one is settled by now.
    paths = {alias: join for alias, join in reads.items() if join}
    for alias, join in paths.items():
        missing[alias] = join.nullable or missing[join.parent]

    select: list[str] = []
    keys: list[str] = []
    clauses: dict[Kind, list[str]] = {kind: [] for kind in KINDS}
    for field_name, column in view.__columns__.items():
        expr = exprs[field_name]
        if isinstance(expr, Column):
            if expr.base is not column.base:
                raise TypeError(
                    f"{name}.{field_name}: declared {column.base.__name__}, "
                    f"the source column is {expr.base.__name__}"
                )
            if (expr.nullable or missing[expr.alias]) and not column.nullable:
                if NotNull not in column.metadata:
                    raise TypeError(
                        f"{name}.{field_name}: the source is nullable; declare `| None`, "
                        "or mark the field NotNull to keep only the rows that have it"
                    )
                clauses["row"].append(f"{expr.sql} IS NOT NULL")
        clauses[expr.kind] += _field_predicates(name, field_name, expr, column)
        select.append(f"{expr.sql} AS {field_name}")
        if expr.kind == "row":
            keys.append(expr.sql)
    if where is not None:
        clauses[where.kind].append(where.sql)

    sql = f"SELECT {', '.join(select)} FROM {_from(joined[0].relation)}"
    by_source_list = set()
    for position, source in enumerate(joined[1:], 1):
        relation = source.relation
        if source.mode == "CROSS JOIN":
            sql += f" CROSS JOIN {_from(relation)}"
            continue
        found = [c for e in joined[:position] for c in _link_conditions(e.relation, relation)]
        if len(found) > 1:
            raise TypeError(
                f"{name}: {len(found)} links relate {relation.__name__} to the other sources; "
                "read it through the link you mean instead"
            )
        if found:
            on, alias, link = found[0]
            by_source_list.add((alias, link))
            how = source.mode
            if position == 1 and joined[0].mode == "LEFT JOIN":  # the first source is optional
                how = "FULL JOIN" if how == "LEFT JOIN" else "RIGHT JOIN"
            sql += f" {how} {_from(relation)} ON {on}"
        elif where is not None and relation.__alias__ in where.reads and source.mode == "JOIN":
            sql += f", {_from(relation)}"  # related by __where__
        else:
            raise TypeError(
                f"{name}: nothing relates {relation.__name__} to the other sources; add a link, "
                f"a __where__ that names it, or say Cross[{relation.__name__}]"
            )
    for alias, join in paths.items():
        if (join.parent, join.link) in by_source_list:
            raise TypeError(
                f"{name}: {join.parent}.{join.link} is read as a path, and its target is "
                "also a source; use one of the two"
            )
        sql += f" {'LEFT JOIN' if missing[alias] else 'JOIN'} {join.sql}"

    for source in sources:
        if source.mode in ("EXISTS", "NOT EXISTS"):
            relation = source.relation
            found = [c for e in joined for c in _link_conditions(e.relation, relation)]
            if len(found) != 1:
                raise TypeError(
                    f"{name}: {len(found)} links relate {relation.__name__} to the other sources; "
                    f"{source.mode} needs exactly one"
                )
            clauses["row"].append(
                f"{source.mode} (SELECT 1 FROM {_from(relation)} WHERE {found[0][0]})"
            )

    if clauses["row"]:
        sql += " WHERE " + " AND ".join(clauses["row"])
    grouped = clauses["aggregate"] or any(e.kind == "aggregate" for e in used)
    if grouped and keys:
        sql += " GROUP BY " + ", ".join(keys)
    if clauses["aggregate"]:
        sql += " HAVING " + " AND ".join(clauses["aggregate"])
    if clauses["window"]:
        sql += " QUALIFY " + " AND ".join(clauses["window"])
    return sql
