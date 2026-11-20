import asyncio
import json
import os
from collections import Counter

import httpx
import pytest

from app.advisor import EXPLANATION_PROMPT, QUESTION_PROMPT
from app.extractor import EXTRACTION_PROMPT
from app.llm import OpenAICompatibleClient
from app.main import app, get_model
from app.report import ADVICE_PROMPT
from scripts.demo import run_scenarios


def test_real_llm_consultation_scenarios(postgres_test_schema, monkeypatch):
    if os.getenv("HUATU_RUN_LIVE_LLM") != "1":
        pytest.skip("真实模型测试单独执行，设置 HUATU_RUN_LIVE_LLM=1")

    backend = OpenAICompatibleClient.from_env()
    counts = Counter()
    successful = Counter()
    kinds = {EXTRACTION_PROMPT: "extraction", EXPLANATION_PROMPT: "explanation",
             QUESTION_PROMPT: "questions", ADVICE_PROMPT: "report_advice"}

    class ObservedModel:
        def complete_json(self, system_prompt, user_input):
            kind = kinds[system_prompt]
            counts[kind] += 1
            result = backend.complete_json(system_prompt, user_input)
            successful[kind] += 1
            return result

    monkeypatch.setitem(app.dependency_overrides, get_model, ObservedModel)

    async def execute():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            return await run_scenarios(client)

    try:
        result = asyncio.run(execute())
    finally:
        print("真实模型调用次数", dict(counts), "返回次数", dict(successful))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert dict(counts) == {"extraction": 11, "explanation": 3, "questions": 7, "report_advice": 4}
    assert successful == counts
    assert all(scene["explanation"]["generated_by"] == "llm" for scene in result.values())
