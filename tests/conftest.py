# 供数据库集成测试共用的 PostgreSQL 隔离夹具。
# 每个测试使用独立 schema，创建表并导入模拟工作簿，结束时删除该 schema。
# 设置 HUATU_TEST_POSTGRES=1 后才运行依赖数据库的用例。

import os
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import sql

from app.db import connect
from app.etl import import_workbook


# 为用例创建独立 schema，建表和导入模拟数据，测试结束后清理全部临时表。
@pytest.fixture
def postgres_test_schema(monkeypatch):
    if os.getenv("HUATU_TEST_POSTGRES") != "1":
        pytest.skip("设置 HUATU_TEST_POSTGRES=1 并配置 PostgreSQL 后运行数据库集成测试")

    identifier = sql.Identifier(f"huatu_test_{uuid4().hex}")
    schema = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
    with connect() as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(identifier))
    try:
        with monkeypatch.context() as config:
            # 业务代码照常创建连接，由搜索路径将表读写限定在本用例的 schema。
            config.setenv("PGOPTIONS", f"-c search_path={identifier.as_string()}")
            with connect() as conn:
                conn.execute(schema.read_text(encoding="utf-8"))
            import_workbook()
            yield
    finally:
        with connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(identifier))
