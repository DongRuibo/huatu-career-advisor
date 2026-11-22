# 验证从 OpenAI 兼容响应到画像落库的过程，模型 HTTP 响应由测试替换。
# 检查有效更新、无效提议过滤，以及坏 JSON 或上游异常时保留已有画像。

from collections import deque
import json

import httpx
import pytest
from pydantic import ValidationError

from app.db import connect
from app.extractor import TurnInput
from app.llm import OpenAICompatibleClient
from app.matcher import match_session
from app.profile import create_session, get_profile, set_manual_preference, submit_turn


# 用固定 HTTP 响应验证正常画像更新，并确认坏 JSON 或上游错误不会写入新的对话轮次。
def test_chat_completions_response_updates_profile_and_failures_preserve_it(postgres_test_schema, monkeypatch):
    valid = {"tag_id": "T001", "value": ["成都"], "strength": "hard", "constraint_type": "in",
             "action": "set", "evidence": "只考虑成都", "speaker": "家长"}
    # 按请求顺序回放正常抽取、部分无效提议、损坏 JSON 和上游 HTTP 错误。
    responses = deque([
        json.dumps({"preferences": [valid]}, ensure_ascii=False),
        json.dumps({"preferences": [
            {**valid, "value": ["重庆"], "evidence": "历史里的证据"},
            {**valid, "value": ["北京"], "evidence": "北京也可以考虑"},
            {**valid, "value": ["重庆"], "evidence": "重庆也可以考虑", "speaker": "学生"},
            {**valid, "value": ["重庆"], "action": "add", "evidence": "重庆也可以考虑"},
        ]}, ensure_ascii=False),
        "{损坏的 JSON",
        503,
    ])
    requests = []

    # 记录请求参数并按顺序返回模拟成功内容或 HTTP 错误，替代真实模型网络调用。
    def mock_post(url, *, headers, json, timeout):
        requests.append(json)
        answer = responses.popleft()
        request = httpx.Request("POST", url)
        if isinstance(answer, int):
            return httpx.Response(answer, request=request)
        return httpx.Response(200, request=request, json={
            "choices": [{"finish_reason": "stop", "message": {"content": answer}}],
        })

    monkeypatch.setattr(httpx, "post", mock_post)
    model = OpenAICompatibleClient("https://mock.invalid/v1", "test-only", "mock")
    session_id = create_session()
    submit_turn(session_id, TurnInput(speaker="家长", text="我们只考虑成都"), model)
    consultant = submit_turn(session_id, TurnInput(speaker="咨询师", text="体制内岗位合适吗？"), model)
    assert not consultant.changes
    changed = submit_turn(session_id, TurnInput(speaker="家长", text="北京也可以考虑，重庆也可以考虑"), model)
    assert [change.new_value for change in changed.changes] == [["成都", "重庆"]]
    original = get_profile(session_id)
    assert set(original.preferences) == {"T001"}
    assert original.preferences["T001"].evidence_text == "重庆也可以考虑"
    assert [change.turn_no for change in original.changes] == [1, 3]

    failed_turn = TurnInput(speaker="家长", text="只考虑重庆")
    with pytest.raises(ValidationError):
        submit_turn(session_id, failed_turn, model)
    with pytest.raises(httpx.HTTPStatusError):
        submit_turn(session_id, failed_turn, model)
    assert get_profile(session_id) == original
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dialogue_turns WHERE session_id = %s", (session_id,)).fetchone() == (3,)
    assert len(requests) == 4 and all(request["response_format"] == {"type": "json_object"} for request in requests)


# 重放 S-C 的城市移除响应，确认只删除重庆并按保留的硬条件重新匹配。
@pytest.mark.parametrize("text", ["算了，还是不考虑重庆了", "把意向城市中的重庆去掉", "麻烦把重庆从备选城市里划掉"])
def test_remove_named_city_keeps_other_hard_filters(postgres_test_schema, monkeypatch, text):
    response = json.dumps({"preferences": [
        {"tag_id": "T001", "value": None, "strength": "soft", "constraint_type": "in", "action": "remove",
         "evidence": text, "speaker": "学生"},
    ]}, ensure_ascii=False)
    model = OpenAICompatibleClient("https://mock.invalid/v1", "test-only", "mock")
    requests = []

    # 记录传给模型的当前选项，然后模拟模型漏填移除值。
    def complete_json(_system_prompt, user_input):
        requests.append(json.loads(user_input)["active_choices"])
        return response

    monkeypatch.setattr(model, "complete_json", complete_json)
    session_id = create_session()
    set_manual_preference(session_id, "T001", ["成都", "重庆"], "hard", "in")
    set_manual_preference(session_id, "T002", ["体制内"], "hard", "in")
    set_manual_preference(session_id, "T003", "偶尔", "hard", "max")
    assert len(match_session(session_id)) == 11

    result = submit_turn(session_id, TurnInput(speaker="学生", text=text), model)
    change, = result.changes
    assert (change.tag_id, change.old_value, change.new_value, change.reason) == (
        "T001", ["成都", "重庆"], ["成都"], text,
    )
    current = get_profile(session_id).preferences
    assert current["T001"].value == ["成都"] and current["T001"].strength == "hard"
    assert current["T002"].value == ["体制内"] and current["T003"].value == "偶尔"
    assert {match.job_id for match in match_session(session_id)} == {"P001", "P005", "P009", "P011", "P012", "P018"}
    assert not submit_turn(session_id, TurnInput(speaker="学生", text=text), model).changes
    assert requests == [
        {"T001": ["成都", "重庆"], "T002": ["体制内"]},
        {"T001": ["成都"], "T002": ["体制内"]},
    ]
