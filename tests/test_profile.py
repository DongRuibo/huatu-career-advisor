import json
from dataclasses import fields

import httpx
import pytest

from app.dictionary import TagDefinition
from app.etl import DEFAULT_WORKBOOK, load_import_data
from app.extractor import PreferenceProposal, TurnInput, extract_turn
from app.llm import OpenAICompatibleClient
from app.profile import merge_preference


class FakeModel:
    def __init__(self, preferences=(), qualifications=None, qualification_evidence=None):
        self.output = {
            "preferences": list(preferences),
            "qualifications": qualifications or {},
            "qualification_evidence": qualification_evidence or {},
        }
        self.inputs = []

    def complete_json(self, system_prompt, user_input):
        self.inputs.append(json.loads(user_input))
        return json.dumps(self.output, ensure_ascii=False)


@pytest.fixture(scope="module")
def dictionary():
    definitions = load_import_data(DEFAULT_WORKBOOK).definitions
    return {
        tag_id: TagDefinition(**{field.name: definition[field.name] for field in fields(TagDefinition)})
        for tag_id, definition in definitions.items()
    }


def proposal(tag_id, value, evidence, *, strength="soft", constraint_type="prefer", action="set", speaker="家长"):
    return {
        "tag_id": tag_id, "value": value, "evidence": evidence, "speaker": speaker,
        "strength": strength, "constraint_type": constraint_type, "action": action,
    }


def test_host_checks_value_evidence_role_and_hard_tag(dictionary, caplog):
    turn = TurnInput(speaker="家长", text="希望  稳定，本科只是岗位要求。")
    context = [{"speaker": "学生", "text": "去成都"}]
    fake = FakeModel([
        proposal("T004", "高", "希望稳定", strength="hard"),
        proposal("T004", "极高", "希望稳定"),
        proposal("T999", "高", "希望稳定"),
        proposal("T001", ["成都"], "去成都", strength="hard", constraint_type="in"),
        proposal("T017", "本科", "本科"),
        proposal("T004", "中", "希望稳定", speaker="学生"),
        proposal("T004", "低", "  "),
        proposal("T002", ["其它"], "希望稳定", strength="hard", constraint_type="in"),
    ])

    output = extract_turn(turn, context, dictionary, fake)

    assert [(p.tag_id, p.value, p.strength, p.evidence) for p in output.preferences] == [
        ("T004", "高", "soft", "希望  稳定")
    ]
    assert len(caplog.records) == 7
    assert not output.qualifications
    assert fake.inputs[0]["current_turn"] == {"speaker": "家长", "text": "希望  稳定，本科只是岗位要求。"}
    assert fake.inputs[0]["recent_turns"] == context
    ordered = next(item for item in fake.inputs[0]["tag_dictionary"] if item["tag_id"] == "T003")
    assert ordered["allowed_values"] == ["无", "偶尔", "频繁"] and ordered["ordered"]


def test_consultant_does_not_call_model(dictionary):
    fake = FakeModel()
    output = extract_turn(TurnInput(speaker="咨询师", text="成都、本科可以吗？"), [], dictionary, fake)
    assert not fake.inputs and not output.preferences and not output.qualifications


def test_add_action_keeps_membership_constraint(dictionary):
    turn = TurnInput(speaker="家长", text="重庆也可以考虑")
    fake = FakeModel([
        proposal("T001", ["重庆"], turn.text, action="add", constraint_type="add"),
        proposal("T001", ["重庆"], turn.text, action="add", constraint_type="in"),
    ])
    result, = extract_turn(turn, [], dictionary, fake).preferences
    assert result.action == "add" and result.constraint_type == "in" and result.value == ["重庆"]


def test_tentative_avoidance_and_overtime_keep_the_approved_shapes(dictionary):
    turn = TurnInput(speaker="家长", text="不太喜欢天天整理资料，加班最好少，偶尔可以接受。")
    overtime = {"preferred": "少", "acceptable_max": "偶尔"}
    away_from_office = {"avoid": "办公室", "needs_confirmation": True}
    avoid_material_management = {"avoid": "材料管理", "needs_confirmation": True}
    fake = FakeModel([
        proposal("T007", away_from_office, "不太喜欢天天整理资料", strength="hard"),
        proposal("T012", avoid_material_management, "不太喜欢天天整理资料"),
        proposal("T013", overtime, "加班最好少，偶尔可以接受", strength="hard"),
        proposal("T013", {"preferred": "偶尔", "acceptable_max": "少"}, "偶尔可以接受"),
        proposal("T007", {"avoid": "办公室", "needs_confirmation": False}, "不太喜欢天天整理资料"),
        proposal("T005", away_from_office, "不太喜欢天天整理资料"),
        proposal("T007", "混合", "不太喜欢天天整理资料"),
        proposal("T012", "窗口服务", "不太喜欢天天整理资料"),
    ])

    output = extract_turn(turn, [], dictionary, fake)
    assert [(p.tag_id, p.value, p.strength) for p in output.preferences] == [
        ("T007", away_from_office, "soft"), ("T012", avoid_material_management, "soft"), ("T013", overtime, "soft"),
    ]
    assert merge_preference(None, output.preferences[0], 1) is None


def test_explicit_office_preference_can_become_active(dictionary):
    turn = TurnInput(speaker="家长", text="希望孩子混合办公。")
    output = extract_turn(turn, [], dictionary, FakeModel([proposal("T007", "混合", "混合办公")]))
    direct, = output.preferences
    assert merge_preference(None, direct, 1).value == "混合"


def test_qualification_evidence_is_checked_field_by_field(dictionary):
    turn = TurnInput(speaker="学生", text="我目前本科，是群众，还没有工作经验。")
    fake = FakeModel(
        qualifications={"education": "硕士", "political_status": "群众", "experience_years": 0},
        qualification_evidence={"education": "我目前本科", "political_status": "是群众", "experience_years": "还没有工作经验"},
    )
    output = extract_turn(turn, [], dictionary, fake)
    assert output.qualifications == {"political_status": "群众", "experience_years": 0}

    later = TurnInput(speaker="学生", text="我有两年工作经验，也希望将来读硕士。")
    fake = FakeModel(
        qualifications={"education": "硕士", "political_status": "群众", "experience_years": 2},
        qualification_evidence={"education": 4, "experience_years": "我有 两 年工作经验"},
    )
    assert extract_turn(later, [], dictionary, fake).qualifications == {"experience_years": 2}


def test_merge_set_add_and_remove_preserve_metadata_and_skip_noop():
    initial = PreferenceProposal(**proposal("T001", ["成都"], "成都", strength="hard", constraint_type="in"))
    first = merge_preference(None, initial, 1)
    contribution = PreferenceProposal(**proposal("T001", ["重庆"], "重庆也可以考虑", action="add"))
    extended = merge_preference(first, contribution, 2)
    assert (extended.value, extended.strength, extended.constraint_type, extended.evidence_turn_id) == (
        ["成都", "重庆"], "hard", "in", 2,
    )
    assert merge_preference(extended, contribution, 3) is extended
    assert first.value == ["成都"]

    remove = PreferenceProposal(**proposal("T001", ["成都"], "成都就先不考虑了", action="remove"))
    remaining = merge_preference(extended, remove, 4)
    assert (remaining.value, remaining.strength) == (["重庆"], "hard")
    assert merge_preference(remaining, remove, 5) is remaining
    changed = merge_preference(remaining, initial, 6)
    assert changed.value == ["成都"]
    clear = PreferenceProposal(**proposal("T001", None, "地区不限制了", action="remove"))
    assert merge_preference(changed, clear, 7) is None


@pytest.mark.parametrize("base_url", ["https://example.test/v1/", "https://example.test/v1/chat/completions"])
def test_chat_client_requests_json_mode_and_accepts_both_url_forms(monkeypatch, base_url):
    def post(url, *, headers, json, timeout):
        assert url == "https://example.test/v1/chat/completions"
        assert headers["Authorization"] == "Bearer test-only"
        assert json["response_format"] == {"type": "json_object"}
        assert json["model"] == "test-model"
        assert json["messages"] == [{"role": "system", "content": "输出 JSON"}, {"role": "user", "content": "测试"}]
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
        })

    monkeypatch.setattr(httpx, "post", post)
    client = OpenAICompatibleClient(base_url, "test-only", "test-model")
    assert client.complete_json("输出 JSON", "测试") == "{}"


@pytest.mark.parametrize("payload", [[], {"choices": {"0": {}}}, {"choices": ["invalid"]}, {"choices": [{"message": None}]}])
def test_chat_client_rejects_malformed_success_envelope(monkeypatch, payload):
    url = "https://example.test/v1/chat/completions"
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: httpx.Response(
        200, request=httpx.Request("POST", url), json=payload,
    ))
    with pytest.raises(ValueError, match="模型"):
        OpenAICompatibleClient(url, "test-only", "test-model").complete_json("输出 JSON", "模拟对话")
