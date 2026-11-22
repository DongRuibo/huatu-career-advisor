# OpenAI 兼容模型调用适配层，从后端环境变量读取地址、凭据和模型名称。
# 统一请求 JSON 对话模式，检查响应状态和消息内容。
# 各业务模块负责解释模型正文并验证自己的响应结构，测试可替换为固定响应。

import os
from typing import Protocol

import httpx


# 业务层依赖同一调用接口，集成测试可传入固定响应模型。
class JsonModel(Protocol):
    # 约定模型实现接收系统提示和用户数据，并以字符串形式返回 JSON 正文。
    def complete_json(self, system_prompt: str, user_input: str) -> str: ...


class OpenAICompatibleClient:
    # 初始化 OpenAI 兼容客户端，将基地址和完整对话接口地址归一到同一请求路径。
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        base_url = base_url.rstrip("/")
        self.endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        self.api_key = api_key
        self.model = model

    # 从后端环境变量构造客户端，缺失的配置项由调用业务处理。
    @classmethod
    def from_env(cls) -> "OpenAICompatibleClient":
        return cls(os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"], os.environ["LLM_MODEL"])

    # 发起一次 JSON 模式对话请求，检查 HTTP 和回复结构后返回第一条消息的正文。
    def complete_json(self, system_prompt: str, user_input: str) -> str:
        response = httpx.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        # 客户端只提取完整的消息内容，各业务模块再校验自己的 JSON 结构。
        # HTTP 成功后仍检查 choices 和结束原因，拒绝被截断的结构化结果。
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices else None
        if not isinstance(choice, dict) or choice.get("finish_reason") == "length":
            raise ValueError("模型未返回完整的 JSON 响应")
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("模型返回的 JSON 内容为空")
        return content
