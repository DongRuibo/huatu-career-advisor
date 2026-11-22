# 岗位查询与维护层，提供条件筛选、岗位详情，以及新增和修改岗位。
# 详情将主表字段、标签事实和知识资料合成一个响应，供接口查询和模型讲解使用。
# 保存时校验标签字典，在同一事务内更新岗位字段和由字段派生的标签。

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from psycopg import Connection, sql
from psycopg.rows import dict_row

from app.db import connect, upsert_rows
from app.dictionary import load_dictionary
from app.etl import JOB_FIELD_TAGS, JOB_HEADERS


class JobNotFoundError(LookupError):
    pass


class JobConflictError(ValueError):
    pass


type Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
WRITABLE_TAGS = tuple(f"T{number:03d}" for number in range(3, 17))


class JobTagInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Text
    evidence: Text | None = None


class JobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Text | None = None
    direction: Text | None = None
    city: Text | None = None
    unit_type: Text | None = None
    unit_name: Text | None = None
    description: Text | None = None
    education_req: Text | None = None
    major_req: Text | None = None
    political_req: Text | None = None
    experience_years: Annotated[int, Field(strict=True, ge=0)] | None = None
    updated_at: datetime | None = None
    remark: Text | None = None
    tags: dict[str, JobTagInput] = Field(default_factory=dict)


class JobCreate(JobUpdate):
    job_id: Text


@dataclass(frozen=True)
class JobDetails:
    job: dict
    tags: list[dict]
    documents: list[dict]


# 使用调用方的连接读取岗位字段、标签和知识资料，找不到岗位时抛出领域错误。
def read_job(conn: Connection, job_id: str) -> JobDetails:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
        job = cursor.fetchone()
        if job is None:
            raise JobNotFoundError("岗位不存在")
        cursor.execute(
            "SELECT jt.tag_id, td.name, jt.value, jt.evidence FROM job_tags jt "
            "JOIN tag_definitions td USING (tag_id) WHERE jt.job_id = %s ORDER BY jt.tag_id",
            (job_id,),
        )
        tags = cursor.fetchall()
        cursor.execute(
            "SELECT doc_id, doc_type, title, content, usage_limit FROM knowledge_docs "
            "WHERE job_id = %s ORDER BY doc_id",
            (job_id,),
        )
        documents = cursor.fetchall()
    return JobDetails(job, tags, documents)


# 为岗位详情查询建立连接，返回按岗位 ID 组装的完整资料。
def get_job(job_id: str) -> JobDetails:
    with connect() as conn:
        return read_job(conn, job_id)


# 组合城市、职业方向和一项标签的可选过滤条件，按岗位 ID 返回列表。
def list_jobs(city: str | None, direction: str | None, tag: tuple[str, str] | None) -> list[dict]:
    conditions = []
    parameters = []
    if city is not None:
        conditions.append(sql.SQL("j.city = %s"))
        parameters.append(city)
    if direction is not None:
        conditions.append(sql.SQL("j.direction = %s"))
        parameters.append(direction)
    if tag is not None:
        # 用 EXISTS 筛选标签，让列表保持一条岗位一行的结构。
        conditions.append(sql.SQL(
            "EXISTS (SELECT 1 FROM job_tags jt WHERE jt.job_id = j.job_id AND jt.tag_id = %s AND jt.value = %s)"
        ))
        parameters.extend(tag)

    query = sql.SQL("SELECT j.* FROM jobs j")
    if conditions:
        query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    with connect() as conn, conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query + sql.SQL(" ORDER BY j.job_id"), parameters)
        return cursor.fetchall()


# 创建或部分更新岗位，在同一事务内校验并同步派生标签，最终返回岗位详情。
def save_job(job_id: str, update: JobUpdate, *, create: bool = False) -> JobDetails:
    # 区分未提供和显式清空。省略标签时保留原值，空对象会清空业务标签。
    changes = update.model_dump(exclude_unset=True, exclude={"job_id", "tags"})
    replace_tags = "tags" in update.model_fields_set
    with connect() as conn:
        dictionary = load_dictionary(conn)
        previous_tags = {}
        if create:
            fields = {key: changes.get(key) for key in JobUpdate.model_fields if key != "tags"}
        else:
            # 锁定岗位后再读取并合并字段，让并发修改按顺序处理。
            if conn.execute("SELECT 1 FROM jobs WHERE job_id = %s FOR UPDATE", (job_id,)).fetchone() is None:
                raise JobNotFoundError("岗位不存在")
            previous = read_job(conn, job_id)
            fields = {key: previous.job[key] for key in JobUpdate.model_fields if key != "tags"}
            fields.update(changes)
            previous_tags = {tag["tag_id"]: tag for tag in previous.tags}

        # 用合并后的岗位字段校验并同步派生标签，随后一并写入数据库。
        synchronized = []
        removed = []
        for tag_id, field in JOB_FIELD_TAGS.items():
            value = fields[field]
            if value is None:
                if tag_id in previous_tags:
                    removed.append(tag_id)
                continue
            definition = dictionary.get(tag_id)
            text_value = str(value)
            if definition is None or text_value not in definition.allowed_values:
                raise ValueError(f"{JOB_HEADERS[field]}超出标签字典允许值")
            if previous_tags.get(tag_id, {}).get("value") != text_value:
                synchronized.append({
                    "job_id": job_id, "tag_id": tag_id, "value": text_value,
                    "evidence": f"岗位资料的{JOB_HEADERS[field]}：{text_value}",
                })

        for tag_id, tag in update.tags.items():
            definition = dictionary.get(tag_id)
            if tag_id not in WRITABLE_TAGS or definition is None or tag.value not in definition.allowed_values:
                raise ValueError(f"岗位标签 {tag_id} 或其取值无效")
            synchronized.append({"job_id": job_id, "tag_id": tag_id, "value": tag.value, "evidence": tag.evidence})

        row = {"job_id": job_id, **fields, "is_matchable": all(fields[key] is not None for key in ("name", "city", "direction"))}
        if create:
            insert = sql.SQL("INSERT INTO jobs ({}) VALUES ({}) ON CONFLICT (job_id) DO NOTHING RETURNING job_id").format(
                sql.SQL(", ").join(map(sql.Identifier, row)), sql.SQL(", ").join(map(sql.Placeholder, row)),
            )
            if conn.execute(insert, row).fetchone() is None:
                raise JobConflictError("岗位 ID 已存在")
        else:
            upsert_rows(conn, "jobs", ("job_id",), [row])

        # 整体替换只删除业务标签。派生标签按字段差异独立更新，值未变则保留已有依据。
        if replace_tags:
            removed.extend(WRITABLE_TAGS)
        if removed:
            conn.execute("DELETE FROM job_tags WHERE job_id = %s AND tag_id = ANY(%s)", (job_id, removed))
        upsert_rows(conn, "job_tags", ("job_id", "tag_id"), synchronized)
        return read_job(conn, job_id)
