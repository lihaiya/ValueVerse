import asyncio

import pytest

from app.core.config import get_settings
from app.services.web_search import (
    MCP_STDIO_READ_LIMIT,
    RuntimeWebSearchConfig,
    _call_mcp_web_search,
    _extract_mcp_result_text,
    _mcp_request,
    _results_from_mcp_payload,
    _validate_mcp_command,
)


def test_extract_mcp_text_content() -> None:
    payload = {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
    assert _extract_mcp_result_text(payload) == "hello\nworld"


def test_results_from_json_text() -> None:
    payload = {
        "content": [
            {
                "type": "text",
                "text": '{"results":[{"title":"MiniMax","url":"https://example.com","snippet":"search result"}]}',
            }
        ]
    }
    results = _results_from_mcp_payload(payload)

    assert len(results) == 1
    assert results[0].title == "MiniMax"
    assert results[0].url == "https://example.com"
    assert results[0].snippet == "search result"


def test_results_from_minimax_organic_text() -> None:
    payload = {
        "content": [
            {
                "type": "text",
                "text": '{"organic":[{"title":"义支付","link":"https://example.com/yizhifu","snippet":"支付结算服务"}]}',
            }
        ]
    }
    results = _results_from_mcp_payload(payload)

    assert len(results) == 1
    assert results[0].title == "义支付"
    assert results[0].url == "https://example.com/yizhifu"
    assert results[0].snippet == "支付结算服务"


def test_results_from_plain_text() -> None:
    results = _results_from_mcp_payload({"content": [{"type": "text", "text": "plain result"}]})

    assert len(results) == 1
    assert results[0].title == "MiniMax Web Search"
    assert results[0].snippet == "plain result"


@pytest.mark.asyncio
async def test_mcp_request_skips_stdout_noise() -> None:
    class FakeStdin:
        def __init__(self) -> None:
            self.lines: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.lines.append(data)

        async def drain(self) -> None:
            await asyncio.sleep(0)

    class FakeStdout:
        def __init__(self) -> None:
            self.lines = iter(
                [
                    b"Starting Minimax MCP server\n",
                    b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n',
                ]
            )

        async def readline(self) -> bytes:
            await asyncio.sleep(0)
            return next(self.lines, b"")

    class FakeProcess:
        stdin = FakeStdin()
        stdout = FakeStdout()
        stderr = None

    result = await _mcp_request(FakeProcess(), 1, "initialize", {}, 1)  # type: ignore[arg-type]

    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_mcp_process_allows_large_json_lines(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        def terminate(self) -> None:
            return None

        async def wait(self) -> int:
            return 0

    async def fake_create_process(*args, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    async def fake_request(*args, **kwargs):
        if args[1] == 1:
            return {"ok": True}
        return {"content": [{"type": "text", "text": "large result"}]}

    async def fake_notify(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.web_search.asyncio.create_subprocess_exec", fake_create_process)
    monkeypatch.setattr("app.services.web_search._mcp_request", fake_request)
    monkeypatch.setattr("app.services.web_search._mcp_notify", fake_notify)

    config = RuntimeWebSearchConfig(
        profile_name="MiniMax",
        provider="minimax_mcp",
        endpoint="https://api.minimaxi.com",
        api_key="secret",
        command="uvx",
        args=("minimax-coding-plan-mcp", "-y"),
        tool_name="web_search",
        timeout_seconds=5,
        max_results=1,
    )
    result = await _call_mcp_web_search(config, "MiniMax")

    assert captured["limit"] == MCP_STDIO_READ_LIMIT
    assert result["content"][0]["text"] == "large result"


def test_production_web_search_blocks_untrusted_command() -> None:
    settings = get_settings()
    original_app_env = settings.app_env
    original_allowed_commands = settings.allowed_web_search_commands
    original_allowed_packages = settings.allowed_web_search_packages
    try:
        settings.app_env = "production"
        settings.allowed_web_search_commands = "uvx"
        settings.allowed_web_search_packages = "minimax-coding-plan-mcp"
        config = RuntimeWebSearchConfig(
            profile_name="bad",
            provider="minimax_mcp",
            endpoint="https://api.minimaxi.com",
            api_key="secret",
            command="powershell",
            args=("minimax-coding-plan-mcp", "-y"),
            tool_name="web_search",
            timeout_seconds=5,
            max_results=1,
        )
        with pytest.raises(RuntimeError, match="command is not allowed"):
            _validate_mcp_command(config)
    finally:
        settings.app_env = original_app_env
        settings.allowed_web_search_commands = original_allowed_commands
        settings.allowed_web_search_packages = original_allowed_packages


def test_production_web_search_blocks_untrusted_mcp_package() -> None:
    settings = get_settings()
    original_app_env = settings.app_env
    original_allowed_commands = settings.allowed_web_search_commands
    original_allowed_packages = settings.allowed_web_search_packages
    try:
        settings.app_env = "production"
        settings.allowed_web_search_commands = "uvx"
        settings.allowed_web_search_packages = "minimax-coding-plan-mcp"
        config = RuntimeWebSearchConfig(
            profile_name="bad package",
            provider="minimax_mcp",
            endpoint="https://api.minimaxi.com",
            api_key="secret",
            command="uvx",
            args=("other-package", "-y"),
            tool_name="web_search",
            timeout_seconds=5,
            max_results=1,
        )
        with pytest.raises(RuntimeError, match="package is not allowed"):
            _validate_mcp_command(config)
    finally:
        settings.app_env = original_app_env
        settings.allowed_web_search_commands = original_allowed_commands
        settings.allowed_web_search_packages = original_allowed_packages
