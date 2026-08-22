from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import UUID

import httpx
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.llm_limits import DEFAULT_LLM_MAX_TOKENS
from app.core.secrets import decrypt_api_key
from app.db.session import get_engine
from app.models import LLMConfigTable


DEFAULT_PRODUCTION_LLM_HOSTS = {
    "minimax": {"api.minimaxi.com"},
    "openai": {"api.openai.com"},
    "ollama": {"localhost", "127.0.0.1", "host.docker.internal", "ollama"},
}


@dataclass(frozen=True)
class RuntimeLLMConfig:
    profile_name: str
    provider: str
    endpoint: str
    model_name: str
    api_key: str | None
    temperature: float
    max_tokens: int

    @property
    def effective_endpoint(self) -> str:
        return normalize_endpoint(self.provider, self.endpoint)


class LLMFactory:
    @classmethod
    def get_config(
        cls,
        workspace_id: UUID | str | None = None,
        owner_user_id: UUID | str | None = None,
    ) -> RuntimeLLMConfig:
        return _get_active_config(
            str(workspace_id) if workspace_id else "",
            str(owner_user_id) if owner_user_id else "",
        )

    @classmethod
    def invalidate(cls) -> None:
        _get_active_config.cache_clear()

    @classmethod
    async def generate(
        cls,
        prompt: str,
        response_format: str | None = None,
        workspace_id: UUID | str | None = None,
        owner_user_id: UUID | str | None = None,
    ) -> str:
        config = cls.get_config(workspace_id=workspace_id, owner_user_id=owner_user_id)
        _validate_llm_endpoint(config)
        if config.provider == "ollama":
            return await _generate_ollama(config, prompt, response_format=response_format)
        if config.provider == "minimax":
            return await _generate_chat_completions_api(config, prompt, response_format=response_format)
        if config.provider == "openai":
            return await _generate_responses_api(config, prompt, response_format=response_format)
        return await _generate_custom(config, prompt)


@lru_cache(maxsize=128)
def _get_active_config(workspace_id: str = "", owner_user_id: str = "") -> RuntimeLLMConfig:
    with Session(get_engine()) as session:
        config = None
        if workspace_id and owner_user_id:
            config = session.exec(
                select(LLMConfigTable).where(
                    LLMConfigTable.workspace_id == UUID(workspace_id),
                    LLMConfigTable.owner_user_id == UUID(owner_user_id),
                    LLMConfigTable.is_active == True,
                )
            ).first()
        if config is None:
            config = session.exec(
                select(LLMConfigTable).where(
                    LLMConfigTable.workspace_id.is_(None),
                    LLMConfigTable.owner_user_id.is_(None),
                    LLMConfigTable.provider == "ollama",
                    LLMConfigTable.is_active == True,
                )
            ).first()
        if config is None:
            return RuntimeLLMConfig(
                "本地 Ollama",
                "ollama",
                "http://localhost:11434",
                "qwen2.5:14b",
                None,
                0.2,
                DEFAULT_LLM_MAX_TOKENS,
            )
        return RuntimeLLMConfig(
            profile_name=(config.profile_name or "未命名配置").strip(),
            provider=config.provider.strip(),
            endpoint=config.endpoint.strip(),
            model_name=config.model_name.strip(),
            api_key=decrypt_api_key(config.api_key),
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )


async def _generate_ollama(config: RuntimeLLMConfig, prompt: str, response_format: str | None = None) -> str:
    endpoint = config.effective_endpoint.rstrip("/")
    num_predict = min(8192, max(1024, config.max_tokens // 4))
    payload = {
        "model": config.model_name,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": config.temperature,
            "num_ctx": config.max_tokens,
            "num_predict": num_predict,
        },
    }
    if response_format == "json":
        payload["format"] = "json"
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"{endpoint}/api/generate",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json().get("response", "")


async def _generate_chat_completions_api(config: RuntimeLLMConfig, prompt: str, response_format: str | None = None) -> str:
    if not config.api_key:
        raise RuntimeError(f"{config.provider} API key is required")
    url = _join_api_path(config.effective_endpoint, "chat/completions")
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    payload = {
        "model": config.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    if response_format == "json":
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError:
            if response_format != "json":
                raise
            payload.pop("response_format", None)
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        return _extract_chat_completion_text(response.json())


async def _generate_responses_api(config: RuntimeLLMConfig, prompt: str, response_format: str | None = None) -> str:
    if not config.api_key:
        raise RuntimeError(f"{config.provider} API key is required")
    url = _join_api_path(config.effective_endpoint, "responses")
    headers = {"Authorization": f"Bearer {config.api_key}"}
    payload = {
        "model": config.model_name,
        "input": prompt,
        "temperature": config.temperature,
        "max_output_tokens": config.max_tokens,
    }
    if response_format == "json":
        payload["text"] = {"format": {"type": "json_object"}}
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return _extract_responses_text(response.json())


async def _generate_custom(config: RuntimeLLMConfig, prompt: str) -> str:
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            config.effective_endpoint,
            headers=headers,
            json={
                "model": config.model_name,
                "prompt": prompt,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data.get("text") or data.get("response") or str(data)


def _extract_responses_text(data: dict[str, object]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    chunks: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict):
                text = content.get("text") or content.get("value")
                if isinstance(text, str):
                    chunks.append(text)
    if chunks:
        return "\n".join(chunks)
    return str(data)


def _extract_chat_completion_text(data: dict[str, object]) -> str:
    choices = data.get("choices")
    if isinstance(choices, list):
        chunks: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    chunks.append(content)
                elif isinstance(content, list):
                    chunks.extend(str(item.get("text")) for item in content if isinstance(item, dict) and item.get("text"))
            text = choice.get("text")
            if isinstance(text, str):
                chunks.append(text)
        if chunks:
            return "\n".join(chunks)
    return str(data)


def _join_api_path(endpoint: str, suffix: str) -> str:
    base = endpoint.rstrip("/")
    if base.endswith(f"/{suffix}"):
        return base
    return f"{base}/{suffix}"


def _validate_llm_endpoint(config: RuntimeLLMConfig) -> None:
    settings = get_settings()
    if not settings.is_production:
        return
    parsed = urlparse(config.effective_endpoint)
    host = (parsed.hostname or "").strip().lower()
    allowed_hosts = set(settings.allowed_llm_hosts_set)
    allowed_hosts.update(DEFAULT_PRODUCTION_LLM_HOSTS.get(config.provider, set()))
    if not host or host not in allowed_hosts:
        raise RuntimeError(f"LLM endpoint host is not allowed in production: {host or '<empty>'}")


def normalize_endpoint(provider: str, endpoint: str) -> str:
    if provider == "minimax" and not endpoint.strip():
        return "https://api.minimaxi.com/v1"
    if provider != "ollama" or not _running_in_container():
        return endpoint
    parsed = urlparse(endpoint)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return endpoint
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"host.docker.internal{port}"
    return urlunparse((parsed.scheme or "http", netloc, parsed.path, "", "", ""))


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists()
