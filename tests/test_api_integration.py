# 通过进程内 HTTP 请求和临时 PostgreSQL schema 验证 FastAPI 的主要业务流程。
# 使用固定模型验证咨询闭环、岗位字段与标签同步、失败回滚及建议显式确认。

import asyncio
import json

import httpx
from psycopg.types.json import Jsonb

from app.advisor import EXPLANATION_PROMPT
from app.db import connect
from app.extractor import EXTRACTION_PROMPT
from app.main import app, get_model
from app.report import ADVICE_PROMPT


class ConsultationModel:
    # 准备保存模型提示词，便于用例核对哪些业务实际触发了模型。
    def __init__(self):
        self.prompts = []

    # 根据提示词类型提供可复现的抽取、讲解或报告响应，用于测试真实接口流程。
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
            result = {
                section: {"text": text, "evidence": [{"source_id": key, "quote": data["sources"][key]}]}
                for section, key, text in (
                    ("overview", "job.city", "这份工作的地点在成都。"),
                    ("responsibilities", "job.description", "这份工作包括会务组织、材料审核与沟通协调。"),
                    ("work_style", "tag.T003", "按现有资料，这个岗位无需出差。"),
                    ("qualifications", "job.education_req", "学历方面可参考本科要求。"),
                )
            }
        else:
            assert system_prompt == ADVICE_PROMPT
            result = {"step_ids": ["compare", "凭空生成的建议"]}
        return json.dumps(result, ensure_ascii=False)


# 使用固定模型验证 Swagger 和从创建会话到生成 JSON、Markdown 报告的咨询流程。
def test_swagger_and_fixed_model_consultation_flow(postgres_test_schema, monkeypatch):
    model = ConsultationModel()
    # 让依赖注入返回此次测试的同一模型实例，便于检查模型调用历史。
    monkeypatch.setitem(app.dependency_overrides, get_model, lambda: model)

    # 通过进程内 HTTP 客户端顺序调用咨询接口，校验画像、六个候选以及报告字段。
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
            assert explanation["overview"] == {
                "text": "这份工作的地点在成都。",
                "evidence": [{"source_id": "job.city", "quote": "工作城市：成都"}],
            }
            assert "收入相对水平：资料未提供" in explanation["missing_information"]
            prompts_before_questions = model.prompts.copy()
            questions = (await client.get(base + "/questions")).json()
            assert model.prompts == prompts_before_questions
            assert len(questions) == 2 and not {question["tag_id"] for question in questions}.intersection(profile["preferences"])
            assert all(question["text"].count("？") == 1 for question in questions)
            assert all(question["tag_name"] in question["text"] for question in questions)
            assert (await client.get(base + "/preferences")).json() == profile
            data = (await client.get(base + "/report?top_n=2")).json()
            assert data["candidate_count"] == 6 and len(data["recommended_jobs"]) == 2
            assert data["hard_preferences"][0]["preference"]["evidence_text"] == "只考虑成都"
            markdown = await client.get(base + "/report?format=md&top_n=6")
            assert markdown.is_success and "P012" in markdown.text and "收入相对水平未知" in markdown.text
            assert data["preference_changes"][0]["turn_no"] == 2
            assert "第 2 轮" not in markdown.text and "只考虑成都" not in markdown.text
            assert (await client.get("/import/issues")).json()["success_count"] == 21

    asyncio.run(execute())


# 复现单项软偏好同分的报告，核对用户展示和完整审计使用同一套匹配事实。
def test_report_explains_single_preference_scores_and_keeps_audit(postgres_test_schema, monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_model, ConsultationModel)

    async def execute():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            session_id = (await client.post("/sessions")).json()["session_id"]
            base = f"/sessions/{session_id}"
            for tag_id, value, strength, constraint in (
                ("T001", ["成都"], "hard", "in"), ("T001", ["成都", "重庆"], "hard", "in"),
                ("T002", ["体制内"], "hard", "in"), ("T003", "偶尔", "hard", "max"),
                ("T005", "多", "soft", "prefer"),
            ):
                result = await client.put(base + f"/preferences/{tag_id}", json={
                    "action": "set", "value": value, "strength": strength, "constraint_type": constraint,
                })
                assert result.is_success, result.text
            with connect() as conn:
                conn.execute("INSERT INTO user_qualifications VALUES (%s, '本科', '群众', 0)", (session_id,))

            matches = (await client.get(base + "/matches")).json()
            audit = (await client.get(base + "/report?format=json")).json()
            assert audit["candidate_count"] == 11
            assert audit["recommended_jobs"] == matches[:5]
            assert [job["job_id"] for job in matches[:5]] == ["P001", "P002", "P009", "P018", "P019"]
            assert all(job["score"] == 1.0 for job in matches[:5])
            p019 = next(job for job in audit["recommended_jobs"] if job["job_id"] == "P019")
            political_hint = next(hint for hint in p019["qualification_hints"] if hint["tag_id"] == "T019")
            assert political_hint["status"] == "reference_only"
            assert audit["preference_changes"][1]["old_value"] == ["成都"]
            assert audit["preference_changes"][1]["new_value"] == ["成都", "重庆"]
            assert matches[0]["match_reasons"][0]["evidence"]

            response = await client.get(base + "/report?format=md")
            assert response.is_success and response.headers["content-type"].startswith("text/markdown")
            report = response.text
            assert "意向地区（成都、重庆）" in report and "出差频率（最多接受偶尔）" in report
            assert "1 项已知软偏好参与评分" in report and "1.00 表示参与评分的偏好均得到满足" in report
            assert "岗位整体适合度" in report and "录用以实际招聘结果为准" in report
            assert all(token not in report for token in ("T001", "用户手动修改", "原始数据第", "→", "得分 1.00"))
            assert report.count("\n| P") == 5
            assert "群众接触程度为多" in report and "参考工作经验要求为2年" in report
            overview, advice = report.split("## 职业规划建议", 1)
            assert political_hint["detail"] in overview
            assert "先对比 P001、P002、P018" in advice and "P009、P019" in advice
            assert "政治面貌" not in advice and "核实哪些经历可计入工作经验" in advice
            assert "选出最看重的一项" in advice

            # 移除一个满分岗位的出差标签，验证报告截取的是按硬条件状态排序后的前五项。
            with connect() as conn:
                conn.execute("DELETE FROM job_tags WHERE job_id = 'P001' AND tag_id = 'T003'")
            mixed = (await client.get(base + "/matches")).json()
            pending = mixed[-1]
            assert (pending["job_id"], pending["hard_condition_status"], pending["score"]) == ("P001", "pending_confirmation", 1.0)
            assert len(mixed) == 11 and mixed[4]["score"] < pending["score"]
            mixed_report = (await client.get(base + "/report?format=json")).json()
            assert mixed_report["recommended_jobs"] == mixed[:5]

    asyncio.run(execute())


# 从原话提交到画像、评分和下一轮追问，覆盖工作类型多选及成长偏好同时生效。
def test_answered_follow_up_topics_are_replaced(postgres_test_schema, monkeypatch):
    model = ConsultationModel()
    monkeypatch.setattr(model, "complete_json", lambda *_: '{"preferences": []}')
    monkeypatch.setitem(app.dependency_overrides, get_model, lambda: model)

    async def execute():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            base = "/sessions/" + (await client.post("/sessions")).json()["session_id"]
            for tag_id, value, strength, constraint in (
                ("T001", ["成都", "重庆"], "hard", "in"), ("T002", ["体制内"], "hard", "in"),
                ("T003", "偶尔", "hard", "max"), ("T004", "高", "soft", "prefer"),
                ("T005", "多", "soft", "prefer"), ("T008", "中上", "soft", "prefer"),
            ):
                updated = await client.put(base + f"/preferences/{tag_id}", json={
                    "action": "set", "value": value, "strength": strength, "constraint_type": constraint,
                })
                assert updated.is_success, updated.text

            first_questions = (await client.get(base + "/questions")).json()
            assert [item["tag_id"] for item in first_questions] == ["T012", "T010"]
            for tag_id, text, value in (
                ("T010", "在成长发展空间方面，我比较倾向于平稳", "平稳"),
                ("T012", "我比较倾向于政务服务、政策研究这样的工作类型", ["政务服务", "政策研究"]),
            ):
                response = await client.post(base + "/turns", json={"speaker": "家长", "text": text})
                assert response.status_code == 201, response.text
                change, = response.json()["changes"]
                assert (change["tag_id"], change["new_value"], change["reason"]) == (tag_id, value, text)
                profile = (await client.get(base + "/preferences")).json()["preferences"]
                assert profile[tag_id]["value"] == value and profile[tag_id]["strength"] == "soft"
                next_questions = (await client.get(base + "/questions")).json()
                assert len(next_questions) == 2 and not {item["tag_id"] for item in next_questions}.intersection(profile)

            matches = (await client.get(base + "/matches")).json()
            assert len(matches) == 11
            work_scores = [item for job in matches for item in job["soft_scores"] if item["tag_id"] == "T012"]
            assert {item["job_value"] for item in work_scores if item["score"] == 1.0} == {"政务服务", "政策研究"}
            assert all(any(item["tag_id"] == "T010" for item in job["soft_scores"]) for job in matches)
            edited = await client.put(base + "/preferences/T012", json={
                "action": "set", "value": ["窗口服务", "政策研究"], "strength": "soft", "constraint_type": "prefer",
            })
            assert edited.is_success, edited.text
            assert edited.json()["preferences"]["T012"]["value"] == ["窗口服务", "政策研究"]

    asyncio.run(execute())


# 覆盖岗位编辑的派生标签同步、校验失败回滚、数据库出错回滚及清空标签。
def test_put_jobs_syncs_derived_fields_and_rolls_back_failed_writes(postgres_test_schema):
    # 创建测试岗位并反复修改，通过详情回查验证成功与失败后的实际数据库状态。
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
            assert response.is_success
            assert {item["job_id"] for item in response.json()} == {"P002", "P006", "P019", "TEST-API"}
            assert (await client.put(path, json={"city": "成都", "tags": {"T005": {"value": "很多"}}})).status_code == 422
            assert (await client.get(path)).json() == updated

            # 让数据库在写标签时中途失败，验证此前对岗位字段的修改也被回滚。
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


# 核查待确认建议的会话隔离、证据轮次及重复确认保护，并测试修改城市后的匹配变化。
def test_explicit_suggestion_confirmation_checks_session_evidence_and_state(postgres_test_schema, monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_model, ConsultationModel)

    # 创建两个会话构造错误确认与合法确认，然后验证建议只有经合法请求才会生效。
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
