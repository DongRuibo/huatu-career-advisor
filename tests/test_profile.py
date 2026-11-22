# 用固定模型和小型标签字典测试抽取校验、偏好合并及模型客户端。
# 重点覆盖原话与角色、未知取值、硬条件限制、待确认结构和异常响应。

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
    # 保存人工构造的模型提议和资格，并初始化请求记录以便核对上下文。
    def __init__(self, preferences=(), qualifications=None, qualification_evidence=None):
        self.output = {
            "preferences": list(preferences),
            "qualifications": qualifications or {},
            "qualification_evidence": qualification_evidence or {},
        }
        self.inputs = []

    # 把传入数据反序列化供断言检查，再返回固定的抽取 JSON。
    def complete_json(self, system_prompt, user_input):
        self.inputs.append(json.loads(user_input))
        return json.dumps(self.output, ensure_ascii=False)


# 根据项目模拟岗位工作簿生成共享字典夹具，让单元测试沿用真实标签定义。
@pytest.fixture(scope="module")
def dictionary():
    definitions = load_import_data(DEFAULT_WORKBOOK).definitions
    return {
        tag_id: TagDefinition(**{field.name: definition[field.name] for field in fields(TagDefinition)})
        for tag_id, definition in definitions.items()
    }


# 按指定值、强度、原话、动作和角色构造一条模型提议，用于组合测试输入。
def proposal(tag_id, value, evidence, *, strength="soft", constraint_type="prefer", action="set", speaker="家长"):
    return {
        "tag_id": tag_id, "value": value, "evidence": evidence, "speaker": speaker,
        "strength": strength, "constraint_type": constraint_type, "action": action,
    }


# 混入无效标签、值、角色与证据，验证程序保留合法提议并规范强度。
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


# 验证咨询师发言跳过模型调用，也不会产生正式偏好或资格。
def test_consultant_does_not_call_model(dictionary):
    fake = FakeModel()
    output = extract_turn(TurnInput(speaker="咨询师", text="成都、本科可以吗？"), [], dictionary, fake)
    assert not fake.inputs and not output.preferences and not output.qualifications


# 同一条真实原话只支持对应标签和值，强度以原话实际限制为准。
def test_evidence_must_support_value_and_strength(dictionary):
    turn = TurnInput(speaker="家长", text="我们只考虑成都。")
    correct, = extract_turn(turn, [], dictionary, FakeModel([
        proposal("T001", ["重庆"], "只考虑成都", strength="hard", constraint_type="in"),
        proposal("T008", "中上", "只考虑成都"),
        proposal("T001", ["成都"], "只考虑成都", constraint_type="in"),
    ])).preferences
    assert (correct.value, correct.strength) == (["成都"], "hard")

    wish = TurnInput(speaker="家长", text="希望以后在成都工作。")
    soft, = extract_turn(wish, [], dictionary, FakeModel([
        proposal("T001", ["成都"], "成都", strength="hard", constraint_type="in"),
    ])).preferences
    assert (soft.value, soft.strength) == (["成都"], "soft")

    negated = TurnInput(speaker="家长", text="不考虑成都，只考虑重庆。")
    remove, = extract_turn(negated, [], dictionary, FakeModel([
        proposal("T001", ["成都"], "成都", strength="hard", constraint_type="in"),
        proposal("T001", ["成都"], "不考虑成都", action="remove", constraint_type="in"),
    ])).preferences
    assert (remove.value, remove.action) == (["成都"], "remove")


# 单个城市移除与清空整个条件分别校验，问句、假设和未知城市均不能补成移除。
@pytest.mark.parametrize(("text", "evidence", "expected"), [
    ("算了，还是不考虑重庆了", "算了，还是不考虑重庆了", [("remove", ["重庆"])]),
    ("我们不再考虑重庆的岗位。", "不再考虑重庆", [("remove", ["重庆"])]),
    ("把意向城市中的重庆去掉", "重庆去掉", [("remove", ["重庆"])]),
    ("麻烦把重庆从备选城市里划掉", "把重庆从备选城市里划掉", [("remove", ["重庆"])]),
    ("请帮我把重庆从意向城市里撤下，可以吗？", "请帮我把重庆从意向城市里撤下", [("remove", ["重庆"])]),
    ("成都保留，重庆删掉", "成都保留，重庆删掉", [("remove", ["重庆"])]),
    ("把意向城市里的重庆取消", "把意向城市里的重庆取消", [("remove", ["重庆"])]),
    ("城市不再限定了。", "城市不再限定了", [("remove", None)]),
    ("不考虑重庆了？", "不考虑重庆了", []),
    ("如果以后不考虑重庆了", "不考虑重庆了", []),
    ("我们不是不考虑重庆", "不考虑重庆", []),
    ("别把重庆从意向城市里去掉", "把重庆从意向城市里去掉", []),
    ("不要取消城市限制", "取消城市限制", []),
    ("重庆以后再讨论", "重庆以后再讨论", []),
    ("不考虑北京了", "不考虑北京了", []),
])
def test_city_removal_requires_explicit_named_option_or_clear(dictionary, text, evidence, expected):
    turn = TurnInput(speaker="学生", text=text)
    fake = FakeModel([proposal("T001", None, evidence, action="remove", constraint_type="in", speaker="学生")])
    result = extract_turn(turn, [], dictionary, fake, active_choices={"T001": ["成都", "重庆"]})
    assert [(item.action, item.value) for item in result.preferences] == expected


# 核对模型填错具体城市、把删除当新增或漏填已不存在的城市时，画像不会误改。
def test_city_removal_does_not_guess_a_target_or_accept_a_conflicting_operation(dictionary):
    text = "成都保留，想把意向城市中的重庆去掉"
    fake = FakeModel([
        proposal("T001", ["成都"], text, action="remove", constraint_type="in"),
        proposal("T001", ["重庆"], text, action="set", constraint_type="in"),
        proposal("T001", None, text, action="remove", constraint_type="in"),
    ])
    result = extract_turn(TurnInput(speaker="家长", text=text), [], dictionary, fake,
                          active_choices={"T001": ["成都"]})
    assert not result.preferences
    assert fake.inputs[0]["active_choices"] == {"T001": ["成都"]}


# 城市只是谈话背景时不写入偏好，其他要求里的强词也不会把城市设成硬限制。
@pytest.mark.parametrize("text", ["我现在住在成都。", "成都的岗位必须有食堂。"])
def test_city_mentioned_without_preference_is_ignored(text, dictionary):
    turn = TurnInput(speaker="家长", text=text)
    fake = FakeModel([proposal("T001", ["成都"], "成都", strength="hard", constraint_type="in")])
    assert extract_turn(turn, [], dictionary, fake).preferences == []


# 校验模型只截取短证据时，相邻的出差上限及学生的群众接触偏好仍能正确识别。
def test_short_evidence_preserves_adjacent_preference_context(dictionary):
    travel = TurnInput(speaker="家长", text="偶尔出差没关系，只要别频繁出差。")
    limit, = extract_turn(travel, [], dictionary, FakeModel([
        proposal("T003", "偶尔", "偶尔出差没关系"),
    ])).preferences
    assert (limit.value, limit.strength, limit.constraint_type) == ("偶尔", "hard", "max")

    contact = TurnInput(speaker="学生", text="我比较喜欢和人沟通，尤其是面向群众做咨询、解释这类工作。")
    preference, = extract_turn(contact, [], dictionary, FakeModel([
        proposal("T005", "多", "面向群众做咨询、解释这类工作", speaker="学生"),
    ])).preferences
    assert (preference.value, preference.strength) == ("多", "soft")


# 验证追加是操作类型，地区约束仍须使用合法的集合约束。
def test_add_action_keeps_membership_constraint(dictionary):
    turn = TurnInput(speaker="家长", text="重庆也可以考虑")
    fake = FakeModel([
        proposal("T001", ["重庆"], turn.text, action="add", constraint_type="add"),
        proposal("T001", ["重庆"], turn.text, action="add", constraint_type="in"),
    ])
    result, = extract_turn(turn, [], dictionary, fake).preferences
    assert result.action == "add" and result.constraint_type == "in" and result.value == ["重庆"]


# 比较各类复合提议，验证待确认避开建议与加班上限的合法结构及生效边界。
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


# 验证明确说出标签允许值时，办公方式可以成为正式软偏好。
def test_explicit_office_preference_can_become_active(dictionary):
    turn = TurnInput(speaker="家长", text="希望孩子混合办公。")
    output = extract_turn(turn, [], dictionary, FakeModel([proposal("T007", "混合", "混合办公")]))
    direct, = output.preferences
    assert merge_preference(None, direct, 1).value == "混合"


# 用完整原话补足模型漏提的成长偏好及少提的工作类型选项。
@pytest.mark.parametrize(("tag_id", "text", "model_values", "expected"), [
    ("T010", "在成长发展空间方面，我比较倾向于平稳", [], "平稳"),
    ("T012", "我比较倾向于政务服务、政策研究这样的工作类型", ["政务服务"], ["政务服务", "政策研究"]),
    ("T012", "我更喜欢窗口服务类工作", [], "窗口服务"),
])
def test_explicit_follow_up_answers_use_all_choices(dictionary, tag_id, text, model_values, expected):
    turn = TurnInput(speaker="家长", text=text)
    fake = FakeModel([proposal(tag_id, value, text) for value in model_values])
    selected, = extract_turn(turn, [], dictionary, fake).preferences
    assert (selected.tag_id, selected.value, selected.strength, selected.evidence) == (tag_id, expected, "soft", text)


@pytest.mark.parametrize("text", [
    "如果成长发展空间平稳，我再考虑。",
    "我不喜欢政务服务、政策研究这样的工作类型",
    "我更倾向政务服务、未知类型这样的工作类型",
])
def test_follow_up_fallback_requires_explicit_dictionary_choices(dictionary, text):
    assert not extract_turn(TurnInput(speaker="家长", text=text), [], dictionary, FakeModel()).preferences


# 逐个校验资格值与本轮证据，验证单项不符时仍可保留其他有效资格。
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


# 依次验证覆盖、追加、部分移除和取消标签，并检查无变化时保留原证据。
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


# 验证基地址和完整接口地址最终产生一致的结构化对话请求。
@pytest.mark.parametrize("base_url", ["https://example.test/v1/", "https://example.test/v1/chat/completions"])
def test_chat_client_requests_json_mode_and_accepts_both_url_forms(monkeypatch, base_url):
    # 代替网络请求，断言地址、认证以及 JSON 请求格式并构造最小合法响应。
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


# 验证 HTTP 成功但回复结构无效的情况仍会被客户端拒绝。
@pytest.mark.parametrize("payload", [[], {"choices": {"0": {}}}, {"choices": ["invalid"]}, {"choices": [{"message": None}]}])
def test_chat_client_rejects_malformed_success_envelope(monkeypatch, payload):
    url = "https://example.test/v1/chat/completions"
    # 为网络替身返回 HTTP 200 和被测包体，单独覆盖成功状态下的响应结构校验。
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: httpx.Response(
        200, request=httpx.Request("POST", url), json=payload,
    ))
    with pytest.raises(ValueError, match="模型"):
        OpenAICompatibleClient(url, "test-only", "test-model").complete_json("输出 JSON", "模拟对话")
