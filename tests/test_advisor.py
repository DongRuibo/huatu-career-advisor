import json

from app.advisor import JOB_FIELDS, explain_job, rank_follow_up_targets
from app.dictionary import TagDefinition
from app.jobs import JobDetails
from app.profile import Preference, Profile


class FixedModel:
    def __init__(self, response):
        self.response = response
        self.inputs = []

    def complete_json(self, system_prompt, user_input):
        self.inputs.append(json.loads(user_input))
        return json.dumps(self.response, ensure_ascii=False)


def test_job_explanation_requires_quotes_from_selected_job_and_filters_prohibited_claims():
    job = dict.fromkeys(JOB_FIELDS) | {
        "job_id": "P012", "name": "综合协调", "city": "成都", "description": "负责会务与协调。",
    }
    details = JobDetails(job, [
        {"tag_id": "T003", "name": "出差频率", "value": "无", "evidence": "模拟设定无需跨城出差"},
    ], [{"doc_id": "K-P012", "content": "需要对接会议安排。测试禁用句：年薪 20 万元，晋升概率 90%。",
         "usage_limit": "资料仅供模拟咨询"}])
    fake = FixedModel({
        "overview": [{"source_id": "job.city", "quote": "工作城市：北京"}],
        "responsibilities": [{"source_id": "doc.K-P012", "quote": "需要对接会议安排"}],
        "work_style": [{"source_id": "doc.K-P012", "quote": "年薪 20 万元，晋升概率 90%"}],
        "qualifications": [{"source_id": "doc.K-P001", "quote": "需要硕士学历"}],
    })

    result = explain_job(details, fake)
    assert result.generated_by == "llm"
    assert "成都" in result.overview.text and "北京" not in result.overview.text
    assert result.responsibilities.text == "需要对接会议安排"
    assert result.responsibilities.evidence[0].source_id == "doc.K-P012"
    assert result.work_style.text == "出差频率：无"
    assert result.qualifications.text == "资料未提供"
    assert "收入相对水平：资料未提供" in result.missing_information
    assert fake.inputs[0]["document_limits"] == {"doc.K-P012": "资料仅供模拟咨询"}
    assert fake.inputs[0]["tag_evidence"] == {"tag.T003": "模拟设定无需跨城出差"}


def test_invalid_job_explanation_output_falls_back_to_supplied_facts():
    job = dict.fromkeys(JOB_FIELDS) | {"job_id": "P021", "name": "综合服务", "experience_years": 0}
    result = explain_job(JobDetails(job, [], []), FixedModel({"overview": "不合规结构"}))
    assert result.generated_by == "fallback"
    assert result.responsibilities.text == "资料未提供"
    assert result.qualifications.text == "经验参考年限：0年"
    assert "岗位知识库资料：资料未提供" in result.missing_information


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
