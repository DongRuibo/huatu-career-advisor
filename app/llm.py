import os
from typing import Protocol

import httpx


class JsonModel(Protocol):
    def complete_json(self, system_prompt: str, user_input: str) -> str: ...


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        base_url = base_url.rstrip("/")
        self.endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        self.api_key = api_key
        self.model = model

    @classmethod
    def from_env(cls) -> "OpenAICompatibleClient":
        return cls(os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"], os.environ["LLM_MODEL"])

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
