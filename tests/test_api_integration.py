import asyncio
import json

import httpx
from psycopg.types.json import Jsonb

from app.advisor import EXPLANATION_PROMPT, QUESTION_PROMPT
from app.db import connect
from app.extractor import EXTRACTION_PROMPT
from app.main import app, get_model
from app.report import ADVICE_PROMPT


class ConsultationModel:
    def __init__(self):
        self.prompts = []

    def complete_json(self, system_prompt, user_input):
        self.prompts.append(system_prompt)
        data = json.loads(user_input)
        if system_prompt == EXTRACTION_PROMPT:
            text = data["current_turn"]["text"]
            if "不太喜欢" in text:
                candidates = [
                    ("T007", {"avoid": "办公室", "needs_confirmation": True}, "soft", "prefer", text),
                    ("T012", {"avoid": "材料管理", "needs_confirmation": True}, "soft", "prefer", text),
                ]
            else:
                candidates = [
                    ("T001", ["成都"], "hard", "in", "只考虑成都"), ("T002", ["体制内"], "hard", "in", "体制内"),
                    ("T003", "偶尔", "hard", "max", "不希望经常出差"), ("T008", "中上", "soft", "prefer", "收入中上更好"),
                ]
            result = {"preferences": [
                {"tag_id": tag_id, "value": value, "strength": strength, "constraint_type": constraint,
                 "evidence": evidence, "speaker": data["current_turn"]["speaker"], "action": "set"}
                for tag_id, value, strength, constraint, evidence in candidates
            ]}
        elif system_prompt == EXPLANATION_PROMPT:
            result = {section: [{"source_id": key, "quote": data["sources"][key]}] for section, key in (
                ("overview", "job.name"), ("responsibilities", "job.description"),
                ("work_style", "tag.T003"), ("qualifications", "job.education_req"),
            )}
        elif system_prompt == QUESTION_PROMPT:
            result = {"questions": [{"tag_id": item["tag_id"], "text": f"您对{item['name']}有什么偏好？"} for item in data]}
            result["questions"][0]["text"] += "这个问题可以稍后回答吗？"
        else:
            assert system_prompt == ADVICE_PROMPT
            result = {"step_ids": ["compare", "凭空生成的建议"]}
        return json.dumps(result, ensure_ascii=False)


def test_swagger_and_fixed_model_consultation_flow(postgres_test_schema, monkeypatch):
    model = ConsultationModel()
    monkeypatch.setitem(app.dependency_overrides, get_model, lambda: model)

    async def execute():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            assert (await client.get("/docs")).status_code == 200
            spec = (await client.get("/openapi.json")).json()
            assert set(spec["paths"]["/jobs/{job_id}"]) == {"get", "put"}
            assert "text/markdown" in spec["paths"]["/sessions/{session_id}/report"]["get"]["responses"]["200"]["content"]
            base = "/sessions/" + (await client.post("/sessions")).json()["session_id"]
            consultant = await client.post(base + "/turns", json={"speaker": "咨询师", "text": "您想去成都吗？"})
            assert consultant.status_code == 201 and not consultant.json()["changes"]
            assert not model.prompts
            response = await client.post(base + "/turns", json={
                "speaker": "家长", "text": "我们只考虑成都的体制内岗位，不希望经常出差，收入中上更好。",
            })
            assert response.status_code == 201 and len(response.json()["changes"]) == 4
            profile = (await client.get(base + "/preferences")).json()
            matches = (await client.get(base + "/matches")).json()
            assert len(matches) == 6
            explanation = (await client.get(base + "/jobs/P012/explain")).json()
            assert explanation["generated_by"] == "llm"
            assert "收入相对水平：资料未提供" in explanation["missing_information"]
            questions = (await client.get(base + "/questions")).json()
            assert len(questions) == 2 and not {question["tag_id"] for question in questions}.intersection(profile["preferences"])
            assert all(question["text"].count("？") == 1 for question in questions)
            assert (await client.get(base + "/preferences")).json() == profile
            data = (await client.get(base + "/report?top_n=2")).json()
            assert data["candidate_count"] == 6 and len(data["recommended_jobs"]) == 2
            assert data["hard_preferences"][0]["preference"]["evidence_text"] == "只考虑成都"
            markdown = await client.get(base + "/report?format=md&top_n=6")
            assert markdown.is_success and "P012" in markdown.text and "收入相对水平未知" in markdown.text
            assert (await client.get("/import/issues")).json()["success_count"] == 21

    asyncio.run(execute())


def test_put_jobs_syncs_derived_fields_and_rolls_back_failed_writes(postgres_test_schema):
    async def execute():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://testserver",
        ) as client:
            job = {"job_id": "TEST-API", "name": "接口案例", "city": "成都", "direction": "体制内",
                   "tags": {"T005": {"value": "多", "evidence": "测试依据"}}}
            created = await client.post("/jobs", json=job)
            assert created.status_code == 201, created.text
            assert (await client.post("/jobs", json=job)).status_code == 409
            path = "/jobs/TEST-API"
            updated = (await client.put(path, json={"city": "重庆", "education_req": "硕士"})).json()
            assert {tag["tag_id"]: tag["value"] for tag in updated["tags"]} == {
                "T001": "重庆", "T002": "体制内", "T005": "多", "T017": "硕士",
            }
            response = await client.get("/jobs", params={"city": "重庆", "direction": "体制内", "tag": "T005:多"})
            assert "TEST-API" in {item["job_id"] for item in response.json()}
            assert (await client.put(path, json={"city": "成都", "tags": {"T005": {"value": "很多"}}})).status_code == 422
            assert (await client.get(path)).json() == updated

            with connect() as conn:
                conn.execute("ALTER TABLE job_tags ADD CONSTRAINT test_write_failure CHECK (false) NOT VALID")
            try:
                failed = await client.put(path, json={"city": "成都", "tags": {"T005": {"value": "少"}}})
                assert failed.status_code == 500
                assert (await client.get(path)).json() == updated
            finally:
                with connect() as conn:
                    conn.execute("ALTER TABLE job_tags DROP CONSTRAINT test_write_failure")

            cleared = (await client.put(path, json={"tags": {}, "education_req": None})).json()
            assert [tag["tag_id"] for tag in cleared["tags"]] == ["T001", "T002"]

    asyncio.run(execute())


def test_explicit_suggestion_confirmation_checks_session_evidence_and_state(postgres_test_schema, monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_model, ConsultationModel)

    async def execute():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            first = (await client.post("/sessions")).json()["session_id"]
            second = (await client.post("/sessions")).json()["session_id"]
            base = f"/sessions/{first}"
            quote = "不太喜欢天天埋头整理资料"
            turn = (await client.post(base + "/turns", json={"speaker": "家长", "text": quote})).json()
            assert not turn["changes"] and {item["tag_id"] for item in turn["pending_suggestions"]} == {"T007", "T012"}
            selection = {"action": "confirm", "evidence_turn_id": turn["turn_id"]}
            assert (await client.put(f"/sessions/{second}/preferences/T007", json=selection)).status_code == 404
            with connect() as conn:
                conn.execute(
                    "INSERT INTO preference_suggestions (session_id, tag_id, value, evidence_turn_id, evidence_text) "
                    "VALUES (%s,'T007',%s,%s,%s)",
                    (second, Jsonb({"avoid": "办公室", "needs_confirmation": True}), turn["turn_id"], quote),
                )
            assert (await client.put(f"/sessions/{second}/preferences/T007", json=selection)).status_code == 404
            assert (await client.put(base + "/preferences/T007", json={**selection, "evidence_turn_id": turn["turn_id"] + 1})).status_code == 404
            confirmation = await client.put(base + "/preferences/T007", json=selection)
            assert confirmation.is_success
            assert confirmation.json()["preferences"]["T007"]["value"]["needs_confirmation"] is False
            assert set(confirmation.json()["pending_suggestions"]) == {"T012"}
            assert (await client.put(base + "/preferences/T007", json=selection)).status_code == 404
            assert (await client.put(base + "/preferences/T001", json={
                "action": "set", "value": ["重庆"], "strength": "hard", "constraint_type": "in",
            })).status_code == 200
            assert all(item["city"] == "重庆" for item in (await client.get(base + "/matches")).json())

    asyncio.run(execute())
