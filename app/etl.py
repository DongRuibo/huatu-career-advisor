# 模拟 Excel 的解析、清洗和导入入口，读取岗位、标签字典、岗位标签和知识资料。
# 先校验表头与取值，合并重复版本并隔离无解冲突，再同步岗位和派生标签。
# 数据库事务同时保存业务记录和导入批次，可按批次追溯缺字段、无效取值和缺失标签。

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import astuple, dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pandas as pd
from psycopg import Connection
from psycopg.rows import dict_row

from app.db import connect, upsert_rows

JOB_COLUMNS = {
    "岗位ID": "job_id",
    "岗位名称": "name",
    "所属方向": "direction",
    "工作城市": "city",
    "单位类型": "unit_type",
    "模拟单位名称": "unit_name",
    "工作内容描述": "description",
    "学历要求": "education_req",
    "专业要求": "major_req",
    "政治面貌要求": "political_req",
    "经验年限要求": "experience_years",
    "更新时间": "updated_at",
    "数据属性": "data_attribute",
    "备注": "remark",
}
JOB_HEADERS = {field: header for header, field in JOB_COLUMNS.items()}
KEY_FIELDS = ("job_id", "name", "city", "direction")
# 这些标签以岗位主表字段为准，导入和岗位编辑共用这份映射。
JOB_FIELD_TAGS = {
    "T001": "city",
    "T002": "direction",
    "T017": "education_req",
    "T018": "experience_years",
    "T019": "political_req",
    "T020": "major_req",
}
DEFINITION_COLUMNS = {
    "标签ID": "tag_id",
    "标签名称": "name",
    "画像分类": "category",
    "数据类型": "data_type",
    "值形式": "value_mode",
    "允许值（|分隔）": "allowed_values",
    "匹配规则分类": "match_rule",
    "岗位数据取值来源": "source",
    "业务解释与未知值规则": "unknown_rule",
}
MATCH_RULES = {
    "明确限定时为硬条件": "硬条件",
    "明确提出上限时为硬条件": "硬条件",
    "偏好排序": "偏好排序",
    "资格参考提示": "资格参考",
}
TAG_COLUMNS = {
    "岗位ID": "job_id",
    "标签ID": "tag_id",
    "岗位标签值": "value",
    "标签事实依据": "evidence",
}
KNOWLEDGE_COLUMNS = {
    "资料ID": "doc_id",
    "关联岗位ID": "job_id",
    "资料类型": "doc_type",
    "资料标题": "title",
    "资料正文（可用于知识库检索）": "content",
    "使用限制": "usage_limit",
}
DEFAULT_WORKBOOK = Path(__file__).resolve().parents[1] / "data" / "测试数据.xlsx"

type ExcelRow = dict[str, str | None]


@dataclass(frozen=True)
class ImportIssue:
    job_id: str | None
    issue_type: str
    detail: str


@dataclass
class ImportData:
    total_rows: int
    jobs: dict[str, dict]
    definitions: dict[str, dict]
    tags: dict[tuple[str, str], dict]
    documents: list[dict]
    unresolved_job_ids: set[str]
    issues: list[ImportIssue]


# 按指定工作表和表头读取原始行，校验列名，将空白归为缺失并记录需要追溯的清洗问题。
def read_rows(
    workbook: pd.ExcelFile,
    sheet: str,
    header_row: int,
    columns: dict[str, str],
    issues: list[ImportIssue],
) -> list[tuple[int, ExcelRow]]:
    # 先按原始文本读取，保留工作表行号，便于定位异常来源。
    frame = pd.read_excel(workbook, sheet_name=sheet, header=header_row - 1, dtype=str, na_filter=False)
    frame.columns = frame.columns.str.strip()
    missing = columns.keys() - set(frame.columns)
    if missing:
        raise ValueError(f"{sheet}第 {header_row} 行缺少表头：{'、'.join(sorted(missing))}")

    selected = frame.loc[:, list(columns)]
    rows = []
    for row_number, cells in enumerate(selected.itertuples(index=False, name=None), header_row + 1):
        stripped = tuple(cell.strip() for cell in cells)
        if not any(stripped):
            continue
        row = dict(zip(columns.values(), (cell or None for cell in stripped), strict=True))
        for header, raw_value, value in zip(columns, cells, stripped, strict=True):
            if raw_value != value:
                issues.append(ImportIssue(
                    row.get("job_id"), "TRIMMED_WHITESPACE",
                    f"{sheet}第 {row_number} 行「{header}」：{raw_value!r} → {value!r}",
                ))
        rows.append((row_number, row))
    return rows


# 验证工作簿字典中的标签、基数、允许值和规则，生成导入与匹配共用的字典。
def read_definitions(rows: list[tuple[int, ExcelRow]]) -> dict[str, dict]:
    # 字典不完整时中止导入。匹配依赖允许值及其顺序，无法用默认值补出这些规则。
    definitions = {}
    for row_number, row in rows:
        tag_id = row["tag_id"]
        if (
            not all(row.values()) or tag_id in definitions
            or row["match_rule"] not in MATCH_RULES or row["value_mode"] not in {"单选", "多选"}
        ):
            raise ValueError(f"标签字典第 {row_number} 行的定义不完整、重复或格式不支持")
        allowed_values = [value.strip() for value in row["allowed_values"].split("|")]
        if not all(allowed_values) or len(set(allowed_values)) != len(allowed_values):
            raise ValueError(f"标签字典第 {row_number} 行的允许值包含空项或重复项")
        definitions[tag_id] = {
            **row,
            "allowed_values": allowed_values,
            "ordered": row["data_type"] == "有序枚举",
            "match_rule": MATCH_RULES[row["match_rule"]],
        }

    missing = JOB_FIELD_TAGS.keys() - definitions.keys()
    if missing:
        raise ValueError(f"标签字典缺少岗位字段对应的标签：{'、'.join(sorted(missing))}")
    return definitions


# 按岗位 ID 合并源行，解析时间并校验枚举，返回有效岗位、派生标签和存在未解决冲突的岗位 ID。
def clean_jobs(
    rows: list[tuple[int, ExcelRow]],
    definitions: dict[str, dict],
    issues: list[ImportIssue],
) -> tuple[dict[str, dict], dict[tuple[str, str], dict], set[str]]:
    grouped = defaultdict(list)
    for row_number, row in rows:
        job_id = row["job_id"]
        if job_id is None:
            # 缺失 ID 的行仍可追溯。同一工作簿重导时会生成相同的内部 ID。
            payload = json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
            job_id = f"__missing_id__:{row_number}:{sha256(payload).hexdigest()[:16]}"

        timestamp = pd.to_datetime(row["updated_at"], format="ISO8601", errors="coerce", utc=True)
        updated_at = None if pd.isna(timestamp) else timestamp.tz_convert(None).to_pydatetime()
        if row["updated_at"] is not None and updated_at is None:
            issues.append(ImportIssue(
                job_id, "INVALID_VALUE",
                f"岗位原始数据第 {row_number} 行更新时间无法解析：{row['updated_at']!r}",
            ))
        grouped[job_id].append((row_number, row, updated_at))

    jobs = {}
    tags = {}
    unresolved = set()
    for job_id, history in grouped.items():
        latest = max((date for _, _, date in history if date is not None), default=None)
        newest = [entry for entry in history if entry[2] == latest]
        row_number, row, updated_at = newest[0]

        if len(history) > 1:
            # 更新时间用于选版本，只有其他字段发生变化才算内容冲突。
            differing = [
                field for field in row if field != "updated_at"
                and any(candidate[field] != row[field] for _, candidate, _ in history)
            ]
            if not differing:
                discarded = [str(number) for number, _, _ in history if number != row_number]
                issues.append(ImportIssue(
                    job_id, "DUPLICATE",
                    f"岗位原始数据保留第 {row_number} 行，合并重复行 {'、'.join(discarded)}",
                ))
            else:
                fields = [*differing, "updated_at"]
                details = "；".join(
                    f"{JOB_HEADERS[field]}（" + "、".join(
                        f"第 {number} 行={candidate[field]!r}" for number, candidate, _ in history
                    ) + "）" for field in fields
                )
                # 日期缺失或最新版本彼此冲突时，隔离该岗位并继续处理其他岗位。
                if any(date is None for _, _, date in history) or any(
                    candidate[field] != row[field] for _, candidate, _ in newest for field in differing
                ):
                    unresolved.add(job_id)
                    issues.append(ImportIssue(job_id, "CONFLICT_UNRESOLVED", details))
                    continue
                issues.append(ImportIssue(
                    job_id, "CONFLICT", f"岗位原始数据保留更新时间最新的第 {row_number} 行。{details}",
                ))

        missing = [JOB_HEADERS[field] for field in KEY_FIELDS if row[field] is None]
        if missing:
            issues.append(ImportIssue(
                job_id, "MISSING_KEY_FIELD",
                f"岗位原始数据第 {row_number} 行缺少：{'、'.join(missing)}",
            ))

        # 数据属性用于辨别源行是否重复。入库字段按岗位表保留，枚举无效值转为空。
        job = {field: value for field, value in row.items() if field != "data_attribute"}
        for tag_id, field in JOB_FIELD_TAGS.items():
            value = job[field]
            if value is None:
                continue
            if value not in definitions[tag_id]["allowed_values"]:
                issues.append(ImportIssue(
                    job_id, "INVALID_VALUE",
                    f"岗位原始数据第 {row_number} 行「{JOB_HEADERS[field]}」{tag_id} 不允许 {value!r}",
                ))
                job[field] = None
                continue
            tags[job_id, tag_id] = {
                "job_id": job_id, "tag_id": tag_id, "value": value,
                "evidence": f"岗位原始数据第 {row_number} 行「{JOB_HEADERS[field]}」：{value}",
            }

        # 已通过字典校验的经验值转为整数。缺关键字段的岗位仍留档供维护接口查询。
        experience_years = job["experience_years"]
        jobs[job_id] = {
            **job,
            "job_id": job_id,
            "experience_years": int(experience_years) if experience_years is not None else None,
            "updated_at": updated_at,
            "is_matchable": not missing and job["city"] is not None and job["direction"] is not None,
        }
    return jobs, tags, unresolved


# 解析 Excel 并整理岗位、标签和知识资料，同时收集异常。产出的中间数据可独立验证，无需数据库。
def load_import_data(path: Path) -> ImportData:
    issues: list[ImportIssue] = []
    with pd.ExcelFile(path, engine="openpyxl") as workbook:
        definitions = read_definitions(read_rows(workbook, "标签字典", 4, DEFINITION_COLUMNS, issues))
        job_rows = read_rows(workbook, "岗位原始数据", 1, JOB_COLUMNS, issues)
        tag_rows = read_rows(workbook, "岗位标签", 4, TAG_COLUMNS, issues)
        doc_rows = read_rows(workbook, "知识库资料", 1, KNOWLEDGE_COLUMNS, issues)

    jobs, tags, unresolved = clean_jobs(job_rows, definitions, issues)
    for row_number, row in tag_rows:
        job_id, tag_id, value = row["job_id"], row["tag_id"], row["value"]
        if job_id in unresolved:
            continue
        # 标签表只补充业务标签，城市、方向和资格标签由岗位字段派生。
        if job_id not in jobs or tag_id not in definitions or tag_id in JOB_FIELD_TAGS:
            issues.append(ImportIssue(
                job_id, "INVALID_VALUE",
                f"岗位标签第 {row_number} 行的岗位或标签来源无效：{job_id!r}、{tag_id!r}",
            ))
            continue
        if value is None:
            continue
        if value not in definitions[tag_id]["allowed_values"]:
            issues.append(ImportIssue(
                job_id, "INVALID_VALUE", f"岗位标签第 {row_number} 行 {tag_id} 不允许 {value!r}",
            ))
            continue
        tags[job_id, tag_id] = row

    # 缺标签只记导入异常，留给匹配层按未知处理。
    for job_id in jobs:
        for tag_id in definitions:
            if (job_id, tag_id) not in tags:
                issues.append(ImportIssue(job_id, "MISSING_TAG", f"{tag_id} 无有效岗位标签，视为未知"))

    documents = []
    for row_number, row in doc_rows:
        job_id = row["job_id"]
        if job_id in unresolved:
            continue
        if (job_id != "ALL" and job_id not in jobs) or not all(
            row[field] for field in ("doc_id", "doc_type", "title", "content")
        ):
            issues.append(ImportIssue(
                job_id, "INVALID_VALUE", f"知识库资料第 {row_number} 行缺少有效关联或必填字段",
            ))
            continue
        # 通用资料保留正文，以空岗位 ID 表示面向所有岗位的资料。
        documents.append({**row, "job_id": None if job_id == "ALL" else job_id})
    return ImportData(len(job_rows), jobs, definitions, tags, documents, unresolved, issues)


# 按批次 ID 或最近时间查询导入结果，并汇总各类异常及其明细。找不到批次时返回空值。
def get_import_report(conn: Connection, run_id: UUID | None = None) -> dict | None:
    # 先定位批次再查问题。由批次是否存在决定返回空问题列表还是未找到。
    with conn.cursor(row_factory=dict_row) as cursor:
        if run_id is None:
            cursor.execute("SELECT * FROM import_runs ORDER BY created_at DESC, run_id DESC LIMIT 1")
        else:
            cursor.execute("SELECT * FROM import_runs WHERE run_id = %s", (run_id,))
        run = cursor.fetchone()
        if run is None:
            return None

        cursor.execute(
            "SELECT job_id, issue_type, detail, created_at FROM import_issues WHERE run_id = %s ORDER BY id",
            (run["run_id"],),
        )
        issues = cursor.fetchall()
    return {**run, "issue_counts": dict(Counter(issue["issue_type"] for issue in issues)), "issues": issues}


# 读取清洗结果后在一个事务内同步业务数据、处理冲突并记下本次导入报告。
def import_workbook(path: Path = DEFAULT_WORKBOOK) -> dict:
    data = load_import_data(path)
    # 岗位、标签、资料和批次报告在同一事务内提交。
    with connect() as conn:
        upsert_rows(conn, "tag_definitions", ("tag_id",), list(data.definitions.values()))
        upsert_rows(conn, "jobs", ("job_id",), list(data.jobs.values()))
        if data.unresolved_job_ids:
            # 已在库的冲突岗位也暂停匹配，防止旧版本继续参与推荐。
            conn.execute(
                "UPDATE jobs SET is_matchable = false, modified_at = now() "
                "WHERE is_matchable AND job_id = ANY(%s)",
                (sorted(data.unresolved_job_ids),),
            )

        # 按本次工作簿同步标签，只清理本批成功导入的岗位。
        existing = conn.execute(
            "SELECT job_id, tag_id FROM job_tags WHERE job_id = ANY(%s)", (list(data.jobs),),
        ).fetchall()
        removed = sorted(set(existing) - data.tags.keys())
        with conn.cursor() as cursor:
            cursor.executemany("DELETE FROM job_tags WHERE job_id = %s AND tag_id = %s", removed)
            upsert_rows(conn, "job_tags", ("job_id", "tag_id"), list(data.tags.values()))
            upsert_rows(conn, "knowledge_docs", ("doc_id",), data.documents)

            cursor.execute(
                "INSERT INTO import_runs (total_rows, success_count) VALUES (%s, %s) RETURNING run_id",
                (data.total_rows, len(data.jobs)),
            )
            run_id = cursor.fetchone()[0]
            cursor.executemany(
                "INSERT INTO import_issues (run_id, job_id, issue_type, detail) VALUES (%s, %s, %s, %s)",
                [(run_id, *astuple(issue)) for issue in data.issues],
            )
        report = get_import_report(conn, run_id)
        assert report is not None
    return report


# 接收可选工作簿路径，调用导入流程并将该批次结果以 JSON 打印到终端。
def main() -> None:
    parser = argparse.ArgumentParser(description="清洗并导入岗位 Excel，输出本批导入报告")
    parser.add_argument("workbook", type=Path, nargs="?", default=DEFAULT_WORKBOOK)
    args = parser.parse_args()
    print(json.dumps(import_workbook(args.workbook), ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
