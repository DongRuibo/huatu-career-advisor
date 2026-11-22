# 验证岗位讲解引用、模型异常回退以及智能追问的标签选择。
# 固定模型响应可复现来源校验，候选标签样本用于验证区分度和覆盖率。

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from app.advisor import JOB_FIELDS, explain_job, follow_up_questions, rank_follow_up_targets
from app.dictionary import TagDefinition
from app.jobs import JobDetails
from app.profile import Preference, Profile


class FixedModel:
    # 保存此用例要返回的模型结果，并准备记录传给模型的输入。
    def __init__(self, response):
        self.response = response
        self.inputs = []

    # 记录模型请求中的资料，按客户端契约返回当前用例配置的 JSON。
    def complete_json(self, system_prompt, user_input):
        self.inputs.append(json.loads(user_input))
        return json.dumps(self.response, ensure_ascii=False)


# 用一段合法改写和三段无效引用，验证正文与原文分开返回，并按段回退。
def test_job_explanation_requires_quotes_from_selected_job_and_filters_prohibited_claims():
    job = dict.fromkeys(JOB_FIELDS) | {
        "job_id": "P012", "name": "综合协调", "city": "成都", "description": "负责会务与协调。",
    }
    details = JobDetails(job, [
        {"tag_id": "T003", "name": "出差频率", "value": "无", "evidence": "模拟设定无需跨城出差"},
    ], [{"doc_id": "K-P012", "content": "需要对接会议安排。测试禁用句：年薪 20 万元，晋升概率 90%。",
         "usage_limit": "资料仅供模拟咨询"}])
    fake = FixedModel({
        "overview": {
            "text": "这是北京的一个综合协调岗位。",
            "evidence": [
                {"source_id": "job.name", "quote": "岗位名称：综合协调"},
                {"source_id": "job.city", "quote": "工作城市：北京"},
            ],
        },
        "responsibilities": {
            "text": "这份工作包括跟进会议安排的对接。",
            "evidence": [{"source_id": "doc.K-P012", "quote": "需要对接会议安排"}],
        },
        "work_style": {
            "text": "可以看看这份工作的待遇。",
            "evidence": [{"source_id": "doc.K-P012", "quote": "年薪 20 万元，晋升概率 90%"}],
        },
        "qualifications": {
            "text": "学历方面可参考硕士要求。",
            "evidence": [{"source_id": "doc.K-P001", "quote": "需要硕士学历"}],
        },
    })

    result = explain_job(details, fake)
    assert result.generated_by == "llm"
    assert "成都" in result.overview.text and "北京" not in result.overview.text
    assert result.responsibilities.text == "这份工作包括跟进会议安排的对接。"
    assert result.responsibilities.evidence[0].source_id == "doc.K-P012"
    assert result.responsibilities.evidence[0].quote == "需要对接会议安排"
    assert result.work_style.text == "这个岗位无需出差。"
    assert result.qualifications.text == "资料未提供"
    assert "收入相对水平：资料未提供" in result.missing_information
    assert fake.inputs[0]["document_limits"] == {"doc.K-P012": "资料仅供模拟咨询"}
    assert fake.inputs[0]["tag_evidence"] == {"tag.T003": "模拟设定无需跨城出差"}


# 核对改写里的事实边界，正常经验表述可通过，违规承诺、无来源和新增数字会回退。
@pytest.mark.parametrize(("text", "cite_experience", "uses_model"), [
    ("工作经验方面可参考 2 年的要求。", True, True),
    ("工作经验方面可参考 5 年的要求。", True, False),
    ("这份工作每月能拿 3 万元。", True, False),
    ("这个岗位的晋升概率是 90%。", True, False),
    ("工作经验方面可参考 2 年的要求。", False, False),
    ("  ", True, False),
])
def test_explanation_rewrite_validates_text_and_requires_evidence(text, cite_experience, uses_model):
    job = dict.fromkeys(JOB_FIELDS) | {"job_id": "P012", "name": "综合协调", "experience_years": 2}
    missing = {"text": "资料未提供", "evidence": []}
    evidence = [{"source_id": "job.experience_years", "quote": "经验参考年限：2年"}] if cite_experience else []
    result = explain_job(JobDetails(job, [], []), FixedModel({
        "overview": missing,
        "responsibilities": missing,
        "work_style": missing,
        "qualifications": {"text": text, "evidence": evidence},
    }))

    assert result.generated_by == ("llm" if uses_model else "fallback")
    assert result.qualifications.text == (text if uses_model else "工作经验方面可参考2年的要求。")
    assert result.qualifications.evidence[0].quote == "经验参考年限：2年"


# 验证同一标签的工作簿原始依据可作引用，跨标签引用和将行号当年限的表述仍被拒绝。
@pytest.mark.parametrize(("source_id", "years", "uses_model"), [
    ("tag.T018", 0, True),
    ("tag.T018", 8, False),
    ("tag.T012", 0, False),
])
def test_explanation_accepts_evidence_for_its_own_tag(source_id, years, uses_model):
    job = dict.fromkeys(JOB_FIELDS) | {"job_id": "P007", "name": "运营管理", "experience_years": 0}
    quote = "岗位原始数据第 8 行「经验年限要求」：0"
    details = JobDetails(job, [
        {"tag_id": "T018", "name": "经验年限要求", "value": "0", "evidence": quote},
        {"tag_id": "T012", "name": "工作内容主类型", "value": "业务运营", "evidence": "岗位日常以业务运营为主。"},
    ], [])
    missing = {"text": "资料未提供", "evidence": []}
    text = f"该岗位的工作经验参考要求为 {years} 年。"

    result = explain_job(details, FixedModel({
        "overview": missing,
        "responsibilities": missing,
        "work_style": missing,
        "qualifications": {"text": text, "evidence": [{"source_id": source_id, "quote": quote}]},
    }))

    assert result.generated_by == ("llm" if uses_model else "fallback")
    assert result.qualifications.text == (text if uses_model else "工作经验方面可参考0年的要求。")
    assert result.qualifications.evidence[0].quote == (quote if uses_model else "经验参考年限：0年")


# 向讲解模块传入结构错误的结果，核对回退内容及日志中的脱敏错误分类。
def test_invalid_job_explanation_output_falls_back_to_supplied_facts(caplog):
    job = dict.fromkeys(JOB_FIELDS) | {"job_id": "P021", "name": "综合服务", "experience_years": 0}
    result = explain_job(JobDetails(job, [], []), FixedModel({"overview": "不合规结构"}))
    assert result.generated_by == "fallback"
    assert result.responsibilities.text == "资料未提供"
    assert result.qualifications.text == "工作经验方面可参考0年的要求。"
    assert "岗位知识库资料：资料未提供" in result.missing_information
    assert "model_type" in caplog.text and "不合规结构" not in caplog.text


# 原文引用正确也不能为错误地点、捏造的职责或反转事实提供依据。
@pytest.mark.parametrize("text", [
    "这份工作在北京，主要负责财务审计。",
    "这份工作在成都，主要负责财务审计。",
    "这份工作不在成都。",
])
def test_valid_citation_does_not_allow_unsupported_prose(text):
    details = JobDetails(dict.fromkeys(JOB_FIELDS) | {"job_id": "P001", "city": "成都"}, [], [])
    sources = [{"source_id": "job.city", "quote": "工作城市：成都"}]
    result = explain_job(details, FixedModel({"overview": {"text": text, "evidence": sources}}))
    assert result.generated_by == "fallback"
    assert result.overview.text == "工作地点在成都。"
    assert result.overview.evidence[0].quote == sources[0]["quote"]


# 一段结构错误只影响本段，合法模型正文及其原文引用照常返回。
def test_explanation_preserves_valid_sections_when_another_has_invalid_shape():
    details = JobDetails(dict.fromkeys(JOB_FIELDS) | {
        "job_id": "P007", "city": "成都", "experience_years": 0,
    }, [], [])
    result = explain_job(details, FixedModel({
        "overview": {"text": "这份工作在成都。", "evidence": [{"source_id": "job.city", "quote": "工作城市：成都"}]},
        "qualifications": "资料未提供",
    }))
    assert result.generated_by == "llm"
    assert result.overview.text == "这份工作在成都。"
    assert result.qualifications.text == "工作经验方面可参考0年的要求。"


# 验证上游状态码可定位请求问题，同时日志保留必要的诊断字段。
def test_explanation_http_error_logs_status_and_falls_back(caplog):
    response = httpx.Response(429, text="测试响应内容勿记录", request=httpx.Request("POST", "https://mock.invalid"))
    model = Mock()
    model.complete_json.side_effect = httpx.HTTPStatusError("测试响应内容勿记录", request=response.request, response=response)
    job = dict.fromkeys(JOB_FIELDS) | {"job_id": "P007", "name": "运营管理"}

    result = explain_job(JobDetails(job, [], []), model)

    assert result.generated_by == "fallback"
    assert "P007" in caplog.text and "HTTP 429" in caplog.text
    assert "测试响应内容勿记录" not in caplog.text


# 验证追问排除已生效偏好和资格标签，并按有效资料覆盖率调整优先级。
def test_follow_up_ranking_excludes_qualifications_and_weights_known_coverage():
    dictionary = {
        tag_id: TagDefinition(tag_id, tag_id, "测试", "单选", ["甲", "乙"], False, rule, "")
        for tag_id, rule in (("T001", "硬条件"), ("T002", "硬条件"), ("T007", "偏好排序"),
                             ("T012", "偏好排序"), ("T017", "资格参考"))
    }
    profile = Profile({"T001": Preference("T001", ["甲"], "hard", "in", None, None)}, {}, [])
    values = [
        {"T001": "甲", "T002": "甲", "T007": "甲", "T012": "甲", "T017": "甲"},
        {"T001": "乙", "T002": "乙", "T007": "乙", "T012": "乙", "T017": "乙"},
        {"T001": "甲", "T002": "甲", "T007": "未知", "T017": "甲"},
        {"T001": "乙", "T002": "乙", "T017": "乙"},
    ]
    targets = rank_follow_up_targets(profile, values, dictionary)
    assert [(item.tag_id, float(item.distinction_score), item.known_jobs) for item in targets] == [
        ("T002", 0.5, 4), ("T007", 0.25, 2),
    ]
    assert rank_follow_up_targets(profile, [], dictionary) == []
    assert rank_follow_up_targets(profile, [values[0], {}], dictionary) == []


# 候选只在加班频率上有区别时，生成的问句应准确指向加班和已知选项。
def test_follow_up_wording_stays_on_selected_tag(monkeypatch):
    candidates = [SimpleNamespace(job_id=job_id, city="成都", direction="体制内") for job_id in ["P001", "P005"]]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value = [("P001", "T013", "少"), ("P005", "T013", "偶尔")]
    definition = TagDefinition("T013", "加班频率", "测试", "单选", ["少", "偶尔"], True, "偏好排序", "")
    monkeypatch.setattr("app.advisor.connect", lambda: connection)
    monkeypatch.setattr("app.advisor.load_dictionary", lambda conn: {"T013": definition})

    question, = follow_up_questions(Profile({}, {}, []), candidates)
    assert question.tag_id == "T013"
    assert question.text == "在加班频率方面，目前候选涉及偶尔、少，您更倾向哪些情况？"
