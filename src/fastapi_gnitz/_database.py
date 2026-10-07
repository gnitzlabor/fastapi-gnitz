"""The database, read and written in the declared relations: rows are their models."""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import partial
from typing import Any, Literal, Self
from uuid import UUID

import gnitz
from gnitz import aio

from fastapi_gnitz._expr import Expr
from fastapi_gnitz._relation import Relation, Table, named, primary_keys, qualified
from fastapi_gnitz._view import View, ddl, held, kind, own

# A write, not yet made: calling it submits it to the connection.
type Write = Callable[[], Awaitable[object]]

# How long the server holds a poll that has nothing new, before it is asked again.
# A connection runs its calls one at a time, so one that holds a poll does nothing
# else that long: it is a connection of its own, and closed without being waited for.
_WAIT = 30.0

# The Python type a column of each gnitz type is declared as.
_PYTHON_TYPES: dict[str, type] = {
    **dict.fromkeys(("U8", "I8", "U16", "I16", "U32", "I32", "U64", "I64", "U128", "I128"), int),
    **dict.fromkeys(("F32", "F64"), float),
    "BOOLEAN": bool,
    "STRING": str,
    "UUID": UUID,
    "BLOB": bytes,
    "DATE": date,
    "TIMESTAMP": datetime,
    "DECIMAL": Decimal,
}


@dataclass(frozen=True, slots=True)
class Delta[R]:
    """A view's value, or how it changed since the last one, from `Database.changes`."""

    reset: bool
    """`added` is the view's whole value: what was read before no longer counts."""
    added: list[R]
    removed: list[R]


class Session:
    """The declared relations, read and written on one gnitz connection: what
    a `Database` and a `Transaction` of it both are."""

    def __init__(
        self,
        client: gnitz.AsyncGnitzClient,
        resolved: dict[type[Relation], tuple[int, gnitz.Schema]],
    ):
        self.client = client
        # What each relation resolved to, once it was found to be as declared.
        self._resolved = resolved

    async def _resolve(self, relation: type[Relation]) -> tuple[int, gnitz.Schema]:
        found = self._resolved.get(relation)
        if found is None:
            found = await self.client.resolve_table(qualified(held(relation)))
            _verify(relation, found[1])
            self._resolved[relation] = found
        return found

    # -- reads -----------------------------------------------------------------

    async def all[R: Relation](
        self,
        relation: type[R],
        *,
        where: object = None,
        order_by: Sequence[object] = (),
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[R]:
        """The rows of a table or a view, as its models::

            await db.all(Sale, where=Sale.customer_id == 3, order_by=[desc(Sale.total)], limit=10)

        `where` and `order_by` are over the relation's own columns: what
        relates it to another relation is a view. Without `order_by` the rows
        come in no particular order.
        """
        await self._resolve(relation)
        sql = _select(relation, where)
        if order_by:
            sql += " ORDER BY " + ", ".join(_own(relation, e, "order_by").sql for e in order_by)
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        if offset is not None:
            sql += f" OFFSET {int(offset)}"
        (result,) = await self.client.execute_sql(sql)
        assert result["type"] == "Rows"
        return _models(relation, result["rows"])

    async def get[R: Relation](self, relation: type[R], key: object) -> R | None:
        """The row with the primary key `key`, or `None`. A compound key is the
        tuple of its columns' values. A view has a key where it declares the
        `PrimaryKey` that gnitz keys it by, as a view grouped by it does."""
        relation_id, schema = await self._resolve(relation)
        keys = primary_keys(relation)
        if not keys:
            raise TypeError(f"{relation.__name__} declares no PrimaryKey to get a row by")
        if len(key if isinstance(key, tuple) else (key,)) != len(keys):
            raise TypeError(f"{relation.__name__} is keyed by {', '.join(keys)}; got {key!r}")
        rows = _models(relation, await self.client.seek(relation_id, schema, key))
        return rows[0] if rows else None

    # -- writes ----------------------------------------------------------------

    async def insert(self, *rows: Table) -> None:
        """Add rows, of any tables. A row whose key exists is refused, and then
        none of the rows is added."""
        await self._push(rows, "error")

    async def upsert(self, *rows: Table) -> None:
        """Add rows, of any tables, each replacing the row that has its key."""
        await self._push(rows, "update")

    async def delete(self, table: type[Table], *keys: object) -> None:
        """Remove the rows of `table` with these primary keys; a compound key
        is a tuple. A key no row has removes nothing."""
        table_id, schema = await self._resolve(_table(table))
        await self._write([partial(self.client.delete, table_id, schema, list(keys))])

    async def _push(self, rows: Iterable[Table], mode: Literal["error", "update"]) -> None:
        by_table: dict[type[Table], list[dict[str, Any]]] = {}
        for row in rows:
            by_table.setdefault(_table(type(row)), []).append(row.__dict__)
        writes: list[Write] = []
        for table, values in by_table.items():
            table_id, schema = await self._resolve(table)
            batch = gnitz.ZSetBatch(schema).extend(values)
            writes.append(partial(self.client.push, table_id, batch, mode))
        await self._write(writes)

    async def _write(self, writes: list[Write]) -> None:
        """Make the writes of one call."""
        raise NotImplementedError


class Transaction(Session):
    """What `Database.transaction` gives: its writes are held, and made
    together when the block is left."""

    def __init__(self, database: Session):
        super().__init__(database.client, database._resolved)
        self._held: list[Write] = []

    async def _write(self, writes: list[Write]) -> None:
        self._held += writes


class Database(Session):
    """A connection to gnitz that speaks in the declared relations::

        async with Database("/var/run/gnitz.sock") as db:
            await db.create(Customer, Sale, CustomerStats)
            await db.insert(Customer(id=1, name="ann"))
            stats = await db.all(CustomerStats)

    It is built on the event loop it is used on, and connected once built.
    `client` is the `gnitz` connection under it, for what this has no verb for.
    It holds the copies of `mirror`, and reads a view from its copy by itself.
    """

    def __init__(self, target: str, *, schema: str = "public"):
        """Connect to `target`. `schema` is where a relation that declares no
        `__schema__` is."""
        super().__init__(aio.connect(target, schema), {})
        self._target = target
        self._following: list[asyncio.Task[None]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        for following in self._following:
            following.cancel()
        await self.client.aclose()

    # -- definition ------------------------------------------------------------

    async def create(self, *relations: type[Relation]) -> None:
        """Create each relation that does not exist, in the order given: a
        table after the tables it links to, a view after what it reads. A
        schema a relation declares is created with it.

        One that exists is left as it is, and refused if its columns or key
        are not the declared ones. A view that exists keeps its definition:
        drop it to change what it selects.
        """
        statements = [ddl(relation, if_not_exists=True) for relation in relations]
        for schema in {r.__schema__ for r in relations if r.__schema__ is not None}:
            try:
                await self.client.create_schema(schema)
            except gnitz.GnitzRefusedError:
                # It is refused where it exists, whoever created it.
                catalog = gnitz.sys_schema(gnitz.SCHEMA_TAB)
                found = {row.name for row in await self.client.scan(gnitz.SCHEMA_TAB, catalog)}
                if schema not in found:
                    raise
        for relation, statement in zip(relations, statements, strict=True):
            await self.client.execute_sql(statement)
            self._resolved.pop(relation, None)
            await self._resolve(relation)

    async def drop(self, *relations: type[Relation]) -> None:
        """Drop each relation, in the order given: a view before what it reads."""
        for relation in relations:
            statement = f"DROP {kind(own(relation))} {qualified(relation)}"
            self._resolved.pop(relation, None)
            await self.client.execute_sql(statement)

    # -- writes ----------------------------------------------------------------

    async def _write(self, writes: list[Write]) -> None:
        if len(writes) == 1:
            await writes[0]()
        elif writes:
            # One call is one write: the rows of several tables go in together.
            # gnitz's transaction is the connection's, and takes in whatever is
            # written while it is open. The connection runs its calls in the
            # order they are made, so made with no await between them, these
            # let no other request's write in.
            transaction = self.client.transaction()
            sent: list[Awaitable[object]] = [transaction.__aenter__()]
            try:
                sent += [write() for write in writes]
            except BaseException as refused:  # as it was made: discard the others
                await transaction.__aexit__(type(refused), refused, None)
                raise
            await asyncio.gather(*sent, transaction.__aexit__(None, None, None))

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """A transaction: what is written through it is written together, or
        not at all::

            async with db.transaction() as tx:
                await tx.insert(sale)
                await tx.delete(Cart, cart_id)

        Leaving the block makes the writes, and it is then that one is
        refused; an exception discards them. A read through `tx` does not see
        them yet.
        """
        transaction = Transaction(self)
        yield transaction
        await self._write(transaction._held)

    # -- copies ----------------------------------------------------------------

    async def mirror(self, directory: str | os.PathLike[str], *views: type[View]) -> None:
        """Hold a copy of each view in `directory`, and read them from it::

            await db.mirror("/var/lib/app/mirror", CustomerStats)
            stats = await db.all(CustomerStats)  # asks the server nothing

        The copies follow the server's views: a commit that changes one is
        in its copy a moment later, so a read just after a write may not show
        it yet. Only a view that declares `__delta__` has a copy; one that is
        dropped has none any more, and is read from the server again.

        The directory is this process's own, and holds the copies from one
        run to the next. Each view is followed on a connection of its own.
        """
        await self.client.mirror_at(os.fspath(directory))
        try:
            for view in views:
                await self.client.mirror_view(qualified(own(view)))
        except BaseException:
            await self.client.close_mirror()
            raise
        self._following += [asyncio.create_task(self._follow(view)) for view in views]

    async def _follow(self, view: type[View]) -> None:
        """Bring the copies up to date whenever `view` changed."""
        view_id, schema = await self._resolve(view)
        bell = aio.connect(self._target, self.client.schema)
        try:
            # The changes since the copy's are read to learn that there are any;
            # it is `poll` that takes them into the copy.
            while (cursor := await self.client.cursor(view_id)) is not None:
                with suppress(gnitz.GnitzDeltaExpiredError):  # which `poll` reads past
                    await bell.delta_poll(view_id, schema, cursor, _WAIT)
                await self.client.poll()
        except gnitz.GnitzNotFoundError:  # dropped: it is read from the server again
            await self.client.forget_view(view_id)
        finally:
            bell.aclose()

    async def changes[R: View](
        self, view: type[R], *, where: object = None
    ) -> AsyncIterator[Delta[R]]:
        """A view's value, then each change to it as it is committed::

            async for delta in db.changes(CustomerStats):
                ...

        The first `Delta` is a reset: the rows the view holds. One follows
        whenever the view changed, and another reset when gnitz no longer
        holds the changes since the last one — the view outran its
        `__delta__`, or was created anew. Only a view that declares
        `__delta__` has changes to read.

        `where` is a condition over the view's columns, as `all` takes one.
        The view is then read as the rows it keeps, and gnitz sends no others:
        a row is added when it comes to meet the condition and removed when it
        no longer does, and a commit that changes none of them is not heard of.

        Each call has a connection of its own for as long as it is iterated.
        """
        select = None if where is None else _select(view, where)
        client = aio.connect(self._target, self.client.schema)
        try:
            while True:
                view_id, schema = await self._resolve(view)
                spec = None  # the view whole
                try:
                    if select is not None:
                        view_id, schema, spec = await client.subscription(select)
                    rows, cursor = await client.delta_bootstrap(view_id, schema, spec)
                    yield Delta(True, _models(view, rows), [])
                    while True:
                        rows, cursor = await client.delta_poll(view_id, schema, cursor, _WAIT, spec)
                        if len(rows):
                            yield Delta(False, _models(view, rows), _models(view, rows, sign=-1))
                except gnitz.GnitzDeltaExpiredError, gnitz.GnitzNotFoundError:
                    # Recreated, it is another relation; resolve it again.
                    self._resolved.pop(view, None)
        finally:
            client.aclose()


def _table[T: type[Table]](table: T) -> T:
    """`table`, if rows are written to it."""
    if not (isinstance(table, type) and issubclass(table, Table)):
        raise TypeError(f"{table!r} is not a table")
    return own(table)


def _select(relation: type[Relation], where: object) -> str:
    """The SELECT of the rows of `relation` that `where` keeps, or of them all."""
    # The columns are the declared ones, in their order, once it resolved.
    sql = f"SELECT * FROM {named(relation)}"
    if where is not None:
        sql += f" WHERE {_own(relation, where, 'where').sql}"
    return sql


def _own(relation: type[Relation], expr: object, what: str) -> Expr:
    """`expr`, if it is one over the columns of `relation` alone."""
    name = relation.__name__
    if not isinstance(expr, Expr) or expr.kind != "row":
        raise TypeError(f"{what}: {expr!r} is not an expression over the columns of {name}")
    if expr.reads.keys() - {relation.__alias__}:
        raise TypeError(
            f"{what}: {expr.sql} reads past {name}; what relates two relations is a view"
        )
    return expr


def _models[R: Relation](relation: type[R], rows: Iterable[Any], *, sign: int = 1) -> list[R]:
    """The rows of the sign as models, each as often as its weight says."""
    return [
        relation.model_validate(row._asdict()) for row in rows for _ in range(row._weight * sign)
    ]


def _verify(relation: type[Relation], schema: gnitz.Schema) -> None:
    """Refuse a relation that is not in the database as it is declared."""
    is_table = issubclass(relation, Table)

    def column(name: str, base: type, nullable: bool, scale: int | None) -> str:
        # Of a view, gnitz says nullable what a filter or a COALESCE keeps from
        # being NULL, and a view's Decimal declares no scale.
        if not is_table:
            return f"{name} {base.__name__}"
        scaled = f"({scale})" if base is Decimal else ""
        return f"{name} {base.__name__}{scaled}{' | None' if nullable else ''}"

    found = [
        column(c.name, _PYTHON_TYPES[gnitz.TypeCode(c.type_code).name], c.is_nullable, c.scale)
        for c in schema.columns
        if not c.is_hidden
    ]
    declared = [
        column(name, c.base, c.nullable, c.precision[1]) for name, c in relation.__columns__.items()
    ]
    keys = primary_keys(relation)
    if is_table or keys:  # a view declares its key only where a row is read by it
        key = [schema.columns[i] for i in schema.pk_indices]
        found.append(f"PRIMARY KEY ({', '.join(c.name for c in key if not c.is_hidden)})")
        declared.append(f"PRIMARY KEY ({', '.join(keys)})")
    if found != declared:
        raise TypeError(
            f"{relation.__name__}: {qualified(relation)} is ({', '.join(found)}); "
            f"declared is ({', '.join(declared)})"
        )
