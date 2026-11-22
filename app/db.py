# PostgreSQL 访问基础层，根据运行环境创建连接，并提供业务表共用的批量写入。
# 写入按主键新增或更新，用空值安全的比较跳过相同记录。
# 事务由调用方的连接上下文管理，便于把多张表的写入一起提交或回滚。

import os
from collections.abc import Sequence

import psycopg
from psycopg import Connection, sql


# 按已经加载的环境变量创建 PostgreSQL 连接，调用方用连接上下文决定事务何时提交。
def connect() -> Connection:
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "huatu_career_advisor"),
        user=os.getenv("POSTGRES_USER", "huatu"),
        password=os.environ["POSTGRES_PASSWORD"],
    )


# 以指定字段作为冲突键批量写入。已有数据仅在实际值变化时更新，空输入直接跳过。
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

    # 用可比较空值的差异判断跳过相同记录，避免重复导入刷新岗位修改时间。
    # 表名和字段名按标识符组合，业务值通过参数绑定。调用方决定整批何时提交。
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
