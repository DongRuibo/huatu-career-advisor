# 命令行端到端演示，读取工作簿中的 S-A、S-B、S-C 模拟发言。
# 每个场景创建独立会话，按顺序提交发言，验证候选基线、讲解、追问和报告。
# 可请求实际启动的 FastAPI，也可由集成测试传入进程内 HTTP 客户端。

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

import httpx
from openpyxl import load_workbook

WORKBOOK = Path(__file__).resolve().parents[1] / "data" / "测试数据.xlsx"
# 每个场景记录检查轮次、该轮候选数和最终候选数。
EXPECTED_COUNTS = {"S-A": (4, 6, 11), "S-B": (4, 17, 17), "S-C": (3, 4, 11)}
EXPLAIN_JOBS = {"S-A": "P012", "S-B": "P009", "S-C": "P008"}


# 统一发送演示中的 HTTP 请求，状态异常时立即停止并保留报错信息。
async def request(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    response = await client.request(method, path, **kwargs)
    response.raise_for_status()
    return response


# 为每组示例建独立会话，逐轮调用接口、核对考核基线并收集展示摘要。
async def run_scenarios(client: httpx.AsyncClient) -> dict:
    workbook = load_workbook(WORKBOOK, read_only=True, data_only=True)
    try:
        scenes = defaultdict(list)
        for scene, _, speaker, text, _ in workbook["模拟咨询对话"].iter_rows(min_row=2, values_only=True):
            scenes[scene].append({"speaker": speaker, "text": text})
    finally:
        workbook.close()
    # 补上基线要求的最后一句，用于验证扩大城市意向后会重新匹配。
    scenes["S-A"].append({"speaker": "家长", "text": "重庆也可以考虑"})

    summaries = {}
    for scene, turns in scenes.items():
        session = (await request(client, "POST", "/sessions")).json()["session_id"]
        base = f"/sessions/{session}"
        checkpoint, expected_initial, expected_final = EXPECTED_COUNTS[scene]
        for number, turn in enumerate(turns, 1):
            response = (await request(client, "POST", f"{base}/turns", json=turn)).json()
            assert response["turn_no"] == number, (scene, response)
            if turn["speaker"] == "咨询师":
                assert not response["changes"] and not response["pending_suggestions"] and not response["qualifications"]
            if number == checkpoint:
                initial = (await request(client, "GET", f"{base}/matches")).json()
                assert len(initial) == expected_initial, (scene, "初始候选", len(initial))

        profile = (await request(client, "GET", f"{base}/preferences")).json()
        matches = (await request(client, "GET", f"{base}/matches")).json()
        assert len(matches) == expected_final, (scene, "最终候选", len(matches), profile["preferences"])
        assert "P021" not in {job["job_id"] for job in matches}
        if scene == "S-B":
            assert not {"T001", "T002", "T007", "T012"}.intersection(profile["preferences"])
            assert set(profile["pending_suggestions"]) == {"T007", "T012"}
            assert profile["preferences"]["T013"]["value"] == {"preferred": "少", "acceptable_max": "偶尔"}
        else:
            assert profile["preferences"]["T001"]["value"] == ["成都", "重庆"]
            assert profile["preferences"]["T003"]["value"] == "偶尔"
        if scene == "S-C":
            assert profile["qualifications"] == {"education": "本科", "political_status": "群众", "experience_years": 0}

        job_id = EXPLAIN_JOBS[scene]
        explanation = (await request(client, "GET", f"{base}/jobs/{job_id}/explain")).json()
        assert explanation["job_id"] == job_id and explanation["overview"]["text"]
        if scene == "S-A":
            assert "收入相对水平：资料未提供" in explanation["missing_information"]
        questions = (await request(client, "GET", f"{base}/questions")).json()
        assert 1 <= len(questions) <= 2
        assert all(item["text"].count("？") + item["text"].count("?") == 1 for item in questions)
        assert not {item["tag_id"] for item in questions}.intersection(profile["preferences"])
        report = (await request(client, "GET", f"{base}/report", params={"format": "json"})).json()
        assert report["candidate_count"] == len(matches) and report["advice"]
        assert [item["job_id"] for item in report["recommended_jobs"]] == [item["job_id"] for item in matches[:5]]
        markdown_checked = scene == "S-C"
        if markdown_checked:
            markdown = await request(client, "GET", f"{base}/report", params={"format": "md", "top_n": 11})
            assert markdown.headers["content-type"].startswith("text/markdown")
            assert all(item in markdown.text for item in ("资格参考", "P008", "本科", "硕士", "职业规划建议"))

        summaries[scene] = {
            "turns": len(turns), "candidate_counts": [len(initial), len(matches)],
            "hard_preferences": {tag: pref["value"] for tag, pref in profile["preferences"].items() if pref["strength"] == "hard"},
            "qualifications": profile["qualifications"], "pending_suggestions": list(profile["pending_suggestions"]),
            "top_jobs": [{"job_id": job["job_id"], "score": job["score"]} for job in matches[:5]],
            "explanation": {"job_id": job_id, "generated_by": explanation["generated_by"],
                            "responsibilities": explanation["responsibilities"]["text"], "missing": explanation["missing_information"]},
            "questions": questions, "advice": report["advice"], "markdown_checked": markdown_checked,
        }
    return summaries


# 通过指定的服务地址运行完整演示，向终端输出可复查的 JSON 结果。
async def run_demo(base_url: str) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=150.0) as client:
        print(json.dumps(await run_scenarios(client), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="通过 FastAPI 跑通 S-A、S-B、S-C 三个咨询场景")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="已启动的 FastAPI 服务地址")
    asyncio.run(run_demo(parser.parse_args().base_url))
