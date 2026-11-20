from collections import deque
import json

import httpx
import pytest
from pydantic import ValidationError

from app.db import connect
from app.extractor import TurnInput
from app.llm import OpenAICompatibleClient
from app.profile import create_session, get_profile, submit_turn


def test_chat_completions_response_updates_profile_and_failures_preserve_it(postgres_test_schema, monkeypatch):
    valid = {"tag_id": "T001", "value": ["成都"], "strength": "hard", "constraint_type": "in",
             "action": "set", "evidence": "只考虑成都", "speaker": "家长"}
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
