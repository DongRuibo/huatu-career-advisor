import os
from collections.abc import Sequence

import psycopg
from psycopg import Connection, sql


def connect() -> Connection:
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "huatu_career_advisor"),
        user=os.getenv("POSTGRES_USER", "huatu"),
        password=os.environ["POSTGRES_PASSWORD"],
    )


def upsert_rows(
    conn: Connection, table: str, key_columns: tuple[str, ...], rows: Sequence[dict]
) -> None:
    if not rows:
        return

    columns = tuple(rows[0])
    target = sql.Identifier(table)
    fields = [sql.Identifier(column) for column in columns if column not in key_columns]
    updates = [sql.SQL("{0} = EXCLUDED.{0}").format(field) for field in fields]
    if table == "jobs":
        updates.append(sql.SQL("modified_at = now()"))

    join = sql.SQL(", ").join
    query = sql.SQL(
        "INSERT INTO {table} ({columns}) VALUES ({params}) "
        "ON CONFLICT ({keys}) DO UPDATE SET {updates} "
        "WHERE ({current}) IS DISTINCT FROM ({incoming})"
    ).format(
        table=target,
        columns=join(map(sql.Identifier, columns)),
        params=join(map(sql.Placeholder, columns)),
        keys=join(map(sql.Identifier, key_columns)),
        updates=join(updates),
        current=join(sql.SQL("{}.{}").format(target, field) for field in fields),
        incoming=join(sql.SQL("EXCLUDED.{}").format(field) for field in fields),
    )
    with conn.cursor() as cursor:
        cursor.executemany(query, rows)
