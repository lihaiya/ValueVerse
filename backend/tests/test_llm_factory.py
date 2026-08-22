import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas import LLMConfigUpdate
from app.services.llm_factory import RuntimeLLMConfig, _extract_chat_completion_text, _generate_chat_completions_api, _validate_llm_endpoint


def test_extract_chat_completion_text() -> None:
    payload = {"choices": [{"message": {"content": "OK"}}]}
    assert _extract_chat_completion_text(payload) == "OK"


def test_llm_config_allows_one_million_token_limit() -> None:
    config = LLMConfigUpdate(
        provider="ollama",
        endpoint="http://localhost:11434",
        model_name="qwen3.6:27b",
        temperature=0.2,
        max_tokens=1048576,
    )
    assert config.max_tokens == 1048576

    with pytest.raises(ValidationError):
        LLMConfigUpdate(
            provider="ollama",
            endpoint="http://localhost:11434",
            model_name="qwen3.6:27b",
            temperature=0.2,
            max_tokens=1048577,
        )


@pytest.mark.asyncio
async def test_minimax_requires_api_key() -> None:
    config = RuntimeLLMConfig(
        profile_name="MiniMax",
        provider="minimax",
        endpoint="https://api.minimaxi.com/v1",
        model_name="MiniMax-M3",
        api_key=None,
        temperature=0.2,
        max_tokens=1024,
    )
    with pytest.raises(RuntimeError, match="API key is required"):
        await _generate_chat_completions_api(config, "hello")


def test_production_llm_endpoint_allowlist_blocks_untrusted_host() -> None:
    settings = get_settings()
    original_app_env = settings.app_env
    original_allowed_llm_hosts = settings.allowed_llm_hosts
    try:
        settings.app_env = "production"
        settings.allowed_llm_hosts = ""
        config = RuntimeLLMConfig(
            profile_name="Custom",
            provider="custom_api",
            endpoint="http://169.254.169.254/latest/meta-data",
            model_name="custom",
            api_key=None,
            temperature=0.2,
            max_tokens=1024,
        )
        with pytest.raises(RuntimeError, match="not allowed"):
            _validate_llm_endpoint(config)
    finally:
        settings.app_env = original_app_env
        settings.allowed_llm_hosts = original_allowed_llm_hosts
