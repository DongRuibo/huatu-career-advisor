import os
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import sql

from app.db import connect
from app.etl import import_workbook


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
            config.setenv("PGOPTIONS", f"-c search_path={identifier.as_string()}")
            with connect() as conn:
                conn.execute(schema.read_text(encoding="utf-8"))
            import_workbook()
            yield
    finally:
        with connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(identifier))
