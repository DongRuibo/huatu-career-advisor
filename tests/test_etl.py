# 验证 Excel 的解析和清洗，覆盖原始模拟工作簿及构造的异常数据。
# 检查表头映射、缺失 ID、非法枚举、重复合并、冲突版本选择和隔离。

from collections import Counter
from datetime import datetime

from openpyxl import load_workbook

from app.etl import DEFAULT_WORKBOOK, load_import_data


# 核对内置模拟 Excel 的岗位数、标签映射、知识资料和预期清洗问题。
def test_supplied_workbook():
    data = load_import_data(DEFAULT_WORKBOOK)

    assert data.total_rows == 22
    assert len(data.jobs) == 21
    assert len(data.definitions) == 20
    assert len(data.tags) == 402
    assert data.jobs["P011"]["city"] == "成都"
    assert data.jobs["P021"]["is_matchable"] is False
    assert data.definitions["T003"]["allowed_values"] == ["无", "偶尔", "频繁"]
    assert data.definitions["T003"]["ordered"] is True
    assert data.tags["P011", "T001"]["value"] == "成都"
    assert data.tags["P001", "T018"]["value"] == "0"
    assert data.jobs["P001"]["experience_years"] == 0
    assert len(data.documents) == 25
    assert sum(document["job_id"] is None for document in data.documents) == 5

    assert Counter(issue.issue_type for issue in data.issues) == {
        "TRIMMED_WHITESPACE": 1, "DUPLICATE": 1, "MISSING_KEY_FIELD": 1, "MISSING_TAG": 18,
    }
    assert next(issue.job_id for issue in data.issues if issue.issue_type == "DUPLICATE") == "P006"
    missing_tags = {
        (issue.job_id, issue.detail.split()[0]) for issue in data.issues if issue.issue_type == "MISSING_TAG"
    }
    expected = {("P012", "T008"), ("P019", "T009"), ("P020", "T011"), ("P021", "T001")}
    expected.update(("P021", f"T{number:03d}") for number in range(3, 17))
    assert missing_tags == expected


# 在临时工作簿中交换字段列并注入无效值，验证表头匹配和缺失 ID 的可重复生成。
def test_bad_values_and_missing_job_id_follow_column_names(tmp_path):
    path = tmp_path / "无效值.xlsx"
    workbook = load_workbook(DEFAULT_WORKBOOK)
    jobs = workbook["岗位原始数据"]
    jobs["A2"] = " "
    jobs["D3"] = " 未知城市 "
    jobs["C4"] = "未知方向"
    # 同时交换表头和值，验证解析按列名识别字段，即使工作表列顺序改变。
    for direction, city in jobs.iter_rows(min_col=3, max_col=4):
        direction.value, city.value = city.value, direction.value
    workbook["岗位标签"]["C19"] = "每天"
    workbook.save(path)

    data = load_import_data(path)
    assert len(data.jobs) == 21
    internal_id = next(job_id for job_id in data.jobs if job_id.startswith("__missing_id__:"))
    assert internal_id in load_import_data(path).jobs
    assert data.jobs[internal_id]["is_matchable"] is False
    assert data.jobs["P002"]["city"] is None
    assert data.jobs["P002"]["is_matchable"] is False
    assert data.jobs["P003"]["direction"] is None
    assert data.jobs["P003"]["is_matchable"] is False
    assert {("P002", "T001"), ("P002", "T003"), ("P003", "T002")}.isdisjoint(data.tags)
    assert any(issue.job_id == internal_id and issue.issue_type == "MISSING_KEY_FIELD" for issue in data.issues)
    assert any(
        issue.job_id == "P002" and issue.issue_type == "INVALID_VALUE" and "每天" in issue.detail
        for issue in data.issues
    )


# 分别验证有明确最新版本时采用新岗位、时间并列冲突时仅隔离该岗位及其关联资料。
def test_conflict_uses_latest_or_quarantines_only_ambiguous_job(tmp_path):
    path = tmp_path / "冲突.xlsx"
    workbook = load_workbook(DEFAULT_WORKBOOK)
    jobs = workbook["岗位原始数据"]
    jobs["B15"] = "新版岗位"
    jobs["L7"] = datetime(2026, 11, 1, 8)
    jobs["L15"] = datetime(2026, 11, 1, 9)
    workbook.save(path)

    data = load_import_data(path)
    assert data.jobs["P006"]["name"] == "新版岗位"
    assert any(issue.job_id == "P006" and issue.issue_type == "CONFLICT" for issue in data.issues)

    jobs["L15"] = jobs["L7"].value
    workbook.save(path)

    ambiguous = load_import_data(path)
    assert ambiguous.unresolved_job_ids == {"P006"}
    assert "P006" not in ambiguous.jobs
    assert len(ambiguous.jobs) == 20
    assert all(job_id != "P006" for job_id, _ in ambiguous.tags)
    assert all(document["job_id"] != "P006" for document in ambiguous.documents)
    issue = next(issue for issue in ambiguous.issues if issue.issue_type == "CONFLICT_UNRESOLVED")
    assert all(
        text in issue.detail for text in ("岗位名称", "第 7 行", "应急现场巡查F", "第 15 行", "新版岗位", "更新时间")
    )
