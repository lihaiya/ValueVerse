import asyncio
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.secrets import decrypt_api_key
from app.db.session import get_engine
from app.models import WebSearchConfigTable
from app.schemas import WebSearchResponse, WebSearchResult


@dataclass(frozen=True)
class RuntimeWebSearchConfig:
    profile_name: str
    provider: str
    endpoint: str
    api_key: str | None
    command: str
    args: tuple[str, ...]
    tool_name: str
    timeout_seconds: int
    max_results: int


class WebSearchClient:
    def __init__(self, workspace_id: UUID | str | None = None) -> None:
        self.workspace_id = str(workspace_id) if workspace_id else ""

    @classmethod
    def get_config(cls, workspace_id: UUID | str | None = None) -> RuntimeWebSearchConfig:
        return _get_active_config(str(workspace_id) if workspace_id else "")

    @classmethod
    def invalidate(cls) -> None:
        _get_active_config.cache_clear()

    async def search(self, query: str, top_k: int | None = None) -> WebSearchResponse:
        config = self.get_config(self.workspace_id)
        started = perf_counter()
        if config.provider != "minimax_mcp":
            raise RuntimeError(f"unsupported web search provider: {config.provider}")
        raw = await _call_mcp_web_search(config, query)
        results = _results_from_mcp_payload(raw)[: top_k or config.max_results]
        return WebSearchResponse(
            query=query,
            provider=config.provider,
            endpoint=config.endpoint,
            results=results,
            latency_ms=int((perf_counter() - started) * 1000),
            raw=raw if isinstance(raw, dict) else {"payload": raw},
        )


@lru_cache(maxsize=128)
def _get_active_config(workspace_id: str = "") -> RuntimeWebSearchConfig:
    with Session(get_engine()) as session:
        config = None
        if workspace_id:
            config = session.exec(
                select(WebSearchConfigTable).where(
                    WebSearchConfigTable.workspace_id == UUID(workspace_id),
                    WebSearchConfigTable.is_active == True,
                )
            ).first()
        if config is None:
            config = session.exec(
                select(WebSearchConfigTable).where(WebSearchConfigTable.workspace_id.is_(None), WebSearchConfigTable.is_active == True)
            ).first()
        if config is None:
            return RuntimeWebSearchConfig(
                profile_name="MiniMax Token Plan Web Search",
                provider="minimax_mcp",
                endpoint="https://api.minimaxi.com",
                api_key=None,
                command="uvx",
                args=("minimax-coding-plan-mcp", "-y"),
                tool_name="web_search",
                timeout_seconds=45,
                max_results=5,
            )
        return RuntimeWebSearchConfig(
            profile_name=(config.profile_name or "MiniMax Web Search").strip(),
            provider=config.provider.strip(),
            endpoint=config.endpoint.strip(),
            api_key=decrypt_api_key(config.api_key),
            command=config.command.strip(),
            args=tuple(str(arg).strip() for arg in (config.args or []) if str(arg).strip()),
            tool_name=config.tool_name.strip(),
            timeout_seconds=config.timeout_seconds,
            max_results=config.max_results,
        )


async def _call_mcp_web_search(config: RuntimeWebSearchConfig, query: str) -> dict[str, Any]:
    if not config.api_key:
        raise RuntimeError("MiniMax Token Plan API key is required for web search")
    _validate_mcp_command(config)
    env = {
        **os.environ,
        "MINIMAX_API_KEY": config.api_key,
        "MINIMAX_API_HOST": config.endpoint.rstrip("/"),
    }
    process = await asyncio.create_subprocess_exec(
        config.command,
        *config.args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        await _mcp_request(
            process,
            1,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "valueverse", "version": "0.1.0"},
            },
            config.timeout_seconds,
        )
        await _mcp_notify(process, "notifications/initialized", {})
        return await _mcp_request(
            process,
            2,
            "tools/call",
            {"name": config.tool_name, "arguments": {"query": query}},
            config.timeout_seconds,
        )
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


async def _mcp_request(
    process: asyncio.subprocess.Process,
    request_id: int,
    method: str,
    params: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    await _write_json_line(process, payload)
    stdout_noise: list[str] = []
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout_seconds)  # type: ignore[union-attr]
        if not line:
            stderr = await _read_stderr(process)
            noise = "; ".join(stdout_noise[-3:])
            details = " ".join(part for part in [stderr, f"stdout: {noise}" if noise else ""] if part)
            raise RuntimeError(f"MCP server exited before response: {details}")
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            stdout_noise.append(text[:300])
            continue
        if data.get("id") != request_id:
            continue
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        result = data.get("result")
        return result if isinstance(result, dict) else {"result": result}


async def _mcp_notify(process: asyncio.subprocess.Process, method: str, params: dict[str, Any]) -> None:
    await _write_json_line(process, {"jsonrpc": "2.0", "method": method, "params": params})


async def _write_json_line(process: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise RuntimeError("MCP stdin is not available")
    process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    await process.stdin.drain()


async def _read_stderr(process: asyncio.subprocess.Process) -> str:
    if process.stderr is None:
        return ""
    try:
        data = await asyncio.wait_for(process.stderr.read(), timeout=0.5)
    except asyncio.TimeoutError:
        return ""
    return data.decode("utf-8", errors="replace")[:1000]


def _results_from_mcp_payload(payload: dict[str, Any]) -> list[WebSearchResult]:
    text = _extract_mcp_result_text(payload)
    parsed = _json_from_text(text)
    if parsed is not None:
        results = _results_from_json(parsed)
        if results:
            return results
    if not text.strip():
        return []
    return [WebSearchResult(title="MiniMax Web Search", url=None, snippet=text.strip()[:1200], raw={"text": text})]


def _extract_mcp_result_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                chunks.append(item["text"])
        return "\n".join(chunks)
    if isinstance(payload.get("text"), str):
        return str(payload["text"])
    return json.dumps(payload, ensure_ascii=False, default=str)


def _json_from_text(text: str) -> object | None:
    stripped = text.strip()
    candidates = [stripped]
    match = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if match:
        candidates.insert(0, match.group(1).strip())
    bracket = re.search(r"(\{.*}|\[.*])", stripped, flags=re.DOTALL)
    if bracket:
        candidates.append(bracket.group(1))
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
    return None


def _results_from_json(data: object) -> list[WebSearchResult]:
    raw_items: list[object] = []
    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        for key in ("results", "items", "data", "web_results", "organic"):
            value = data.get(key)
            if isinstance(value, list):
                raw_items = value
                break
        if not raw_items:
            raw_items = [data]
    results: list[WebSearchResult] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or item.get("source") or "Web Search Result").strip()
        url = item.get("url") or item.get("link") or item.get("href")
        snippet = str(item.get("snippet") or item.get("summary") or item.get("content") or item.get("text") or "").strip()
        if not snippet:
            snippet = json.dumps(item, ensure_ascii=False, default=str)[:500]
        results.append(
            WebSearchResult(
                title=title[:200],
                url=str(url).strip() if url else None,
                snippet=snippet[:1200],
                raw=item,
            )
        )
    return results


def _validate_mcp_command(config: RuntimeWebSearchConfig) -> None:
    settings = get_settings()
    if not settings.is_production:
        return
    command_name = Path(config.command).name.lower()
    if command_name not in settings.allowed_web_search_commands_set:
        raise RuntimeError(f"web search command is not allowed in production: {command_name}")
    package_name = next((arg.strip().lower() for arg in config.args if arg.strip() and not arg.strip().startswith("-")), "")
    if package_name not in settings.allowed_web_search_packages_set:
        raise RuntimeError(f"web search MCP package is not allowed in production: {package_name or '<empty>'}")
