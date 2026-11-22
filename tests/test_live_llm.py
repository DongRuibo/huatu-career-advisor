# 可选真实模型验收，使用模拟工作簿和隔离的本地数据库运行三组完整咨询。
# 启用后统计各类模型请求和成功响应，验证场景结果及讲解实际使用模型。
# 设置 HUATU_RUN_LIVE_LLM=1 后才调用真实模型服务。

import asyncio
import json
import os
from collections import Counter

import httpx
import pytest

from app.advisor import EXPLANATION_PROMPT
from app.extractor import EXTRACTION_PROMPT
from app.llm import OpenAICompatibleClient
from app.main import app, get_model
from app.report import ADVICE_PROMPT
from scripts.demo import run_scenarios


# 显式启用时运行三组真实模型场景，核对调用数量、模型成功响应及演示结果。
def test_real_llm_consultation_scenarios(postgres_test_schema, monkeypatch):
    if os.getenv("HUATU_RUN_LIVE_LLM") != "1":
        pytest.skip("真实模型测试单独执行，设置 HUATU_RUN_LIVE_LLM=1")

    backend = OpenAICompatibleClient.from_env()
    counts = Counter()
    successful = Counter()
    kinds = {EXTRACTION_PROMPT: "extraction", EXPLANATION_PROMPT: "explanation", ADVICE_PROMPT: "report_advice"}

    class ObservedModel:
        # 代理真实模型请求，按业务类型分别累计尝试数和成功响应数。
        def complete_json(self, system_prompt, user_input):
            kind = kinds[system_prompt]
            counts[kind] += 1
            result = backend.complete_json(system_prompt, user_input)
            successful[kind] += 1
            return result

    monkeypatch.setitem(app.dependency_overrides, get_model, ObservedModel)

    # 通过进程内 FastAPI 客户端运行通用演示流程，该流程会使用注入的真实模型。
    async def execute():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            return await run_scenarios(client)

    try:
        result = asyncio.run(execute())
    finally:
        print("真实模型调用次数", dict(counts), "返回次数", dict(successful))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert dict(counts) == {"extraction": 11, "explanation": 3, "report_advice": 4}
    assert successful == counts
    assert all(scene["explanation"]["generated_by"] == "llm" for scene in result.values())
