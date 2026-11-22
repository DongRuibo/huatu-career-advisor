# 回放原始工作簿的三组咨询，使用固定模型响应验证 FastAPI 与 PostgreSQL 的业务闭环。

import asyncio
import json

import httpx
import pytest
from openpyxl import load_workbook

from app.extractor import EXTRACTION_PROMPT
from app.main import app, get_model
from scripts.demo import EXPECTED_COUNTS, WORKBOOK, request


class ScenarioModel:
    # 按发言开头匹配预设提议，仍由实际抽取层核对原话、取值和角色。
    def complete_json(self, system_prompt, user_input):
        assert system_prompt == EXTRACTION_PROMPT
        turn = json.loads(user_input)["current_turn"]
        text = turn["text"]
        groups = {
            "我们只考虑成都市": [
                ("T001", ["成都"], "只考虑成都市", "hard", "in", "set"),
                ("T002", ["体制内"], "只考虑成都市的体制内岗位", "hard", "in", "set"),
                ("T003", "偶尔", "不希望经常出差", "hard", "max", "set"),
                ("T004", "高", "尽量稳定", "soft", "prefer", "set"),
            ],
            "我比较喜欢和人沟通": [("T005", "多", "尤其是面向群众做咨询、解释这类工作", "soft", "prefer", "set")],
            "收入如果": [("T008", "中上", "收入如果在当地算中上就更好", "soft", "prefer", "set")],
            "重庆也可以考虑": [("T001", ["重庆"], "重庆也可以考虑", "soft", "in", "add")],
            "孩子不太喜欢": [
                ("T007", {"avoid": "办公室", "needs_confirmation": True}, "不太喜欢天天埋头整理资料", "soft", "prefer", "set"),
                ("T012", {"avoid": "材料管理", "needs_confirmation": True}, "不太喜欢天天埋头整理资料", "soft", "prefer", "set"),
                ("T005", "多", "希望多和不同的人打交道", "soft", "prefer", "set"),
                ("T006", "一般", "工作节奏不要太紧张", "soft", "max", "set"),
            ],
            "城市和职业方向都可以讨论": [("T003", "偶尔", "不希望经常跨城市出差", "hard", "max", "set")],
            "加班最好": [("T013", {"preferred": "少", "acceptable_max": "偶尔"}, text, "soft", "prefer", "set")],
            "我们未来只考虑": [
                ("T002", ["体制内"], "只考虑体制内", "hard", "in", "set"),
                ("T001", ["成都"], "工作城市必须成都", "hard", "in", "set"),
                ("T003", "无", "完全不能出差", "hard", "max", "set"),
                ("T005", "多", "孩子喜欢和群众接触", "soft", "prefer", "set"),
            ],
            "我现在本科学历": [],
            "重新考虑了一下": [
                ("T001", ["重庆"], "重庆也可以接受", "soft", "in", "add"),
                ("T003", "偶尔", "偶尔出差没关系，只要别频繁出差", "hard", "max", "set"),
            ],
            "岗位如果要求": [],
        }
        proposals, = [group for prefix, group in groups.items() if text.startswith(prefix)]
        result = {"preferences": [
            {"tag_id": tag_id, "value": value, "evidence": evidence, "strength": strength,
             "constraint_type": constraint, "action": action, "speaker": turn["speaker"]}
            for tag_id, value, evidence, strength, constraint, action in proposals
        ]}
        if text.startswith("我现在本科学历"):
            result["qualifications"] = {"education": "本科", "political_status": "群众", "experience_years": 0}
            result["qualification_evidence"] = {
                "education": "我现在本科学历", "political_status": "政治面貌是群众", "experience_years": "还没有工作经验",
            }
        return json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("scene", ["S-A", "S-B", "S-C"])
def test_workbook_scenario_with_fixed_model(postgres_test_schema, monkeypatch, scene):
    monkeypatch.setitem(app.dependency_overrides, get_model, ScenarioModel)
    with WORKBOOK.open("rb") as source:
        workbook = load_workbook(source, read_only=True, data_only=True)
        turns = [
            {"speaker": speaker, "text": text}
            for scene_id, _, speaker, text, _ in workbook["模拟咨询对话"].iter_rows(min_row=2, values_only=True)
            if scene_id == scene
        ]
        workbook.close()
    if scene == "S-A":
        turns.append({"speaker": "家长", "text": "重庆也可以考虑"})

    async def execute():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            base = "/sessions/" + (await request(client, "POST", "/sessions")).json()["session_id"]
            checkpoint, first_count, final_count = EXPECTED_COUNTS[scene]
            for number, turn in enumerate(turns, 1):
                result = (await request(client, "POST", base + "/turns", json=turn)).json()
                assert result["turn_no"] == number
                if turn["speaker"] == "咨询师":
                    assert not (result["changes"] or result["pending_suggestions"] or result["qualifications"])
                if number == checkpoint:
                    initial = (await request(client, "GET", base + "/matches")).json()
                    assert len(initial) == first_count
            profile = (await request(client, "GET", base + "/preferences")).json()
            final = (await request(client, "GET", base + "/matches")).json()
            assert len(final) == final_count
            if scene == "S-A":
                change, = result["changes"]
                assert (change["tag_id"], change["old_value"], change["new_value"]) == ("T001", ["成都"], ["成都", "重庆"])
                assert set(profile["preferences"]) == {"T001", "T002", "T003", "T004", "T005", "T008"}
                assert profile["preferences"]["T001"]["strength"] == "hard"
                for matches in (initial, final):
                    p012 = next(job for job in matches if job["job_id"] == "P012")
                    assert p012["score"] == 0.75 and p012["unknown_tags"] == ["T008"]
            elif scene == "S-B":
                assert set(profile["preferences"]) == {"T003", "T005", "T006", "T013"}
                assert set(profile["pending_suggestions"]) == {"T007", "T012"}
                for tag, suggestion in profile["pending_suggestions"].items():
                    confirmed = (await request(client, "PUT", base + f"/preferences/{tag}", json={
                        "action": "confirm", "evidence_turn_id": suggestion["evidence_turn_id"],
                    })).json()
                    assert tag not in confirmed["pending_suggestions"]
                    assert confirmed["preferences"][tag]["value"] == {**suggestion["value"], "needs_confirmation": False}
            else:
                assert {job["job_id"] for job in initial} == {"P001", "P005", "P012", "P018"}
                assert profile["qualifications"] == {"education": "本科", "political_status": "群众", "experience_years": 0}
                assert set(profile["preferences"]) == {"T001", "T002", "T003", "T005"}
                assert profile["preferences"]["T001"]["value"] == ["成都", "重庆"]
                assert profile["preferences"]["T003"]["value"] == "偶尔"
                p019 = next(job for job in final if job["job_id"] == "P019")
                hint = next(hint for hint in p019["qualification_hints"] if hint["tag_id"] == "T019")
                assert hint["status"] == "reference_only" and "需以实际招聘公告为准" in hint["detail"]

    asyncio.run(execute())
