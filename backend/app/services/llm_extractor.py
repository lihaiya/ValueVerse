import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from app.services.llm_factory import LLMFactory
from app.services.metadata import run_financial_reconciliation


DOCUMENT_CHAR_LIMIT = 52000
REQUIRED_SECTION_HINTS = (
    "投资摘要",
    "公司与报告",
    "财务表现",
    "分业务",
    "管理层战略",
    "风险事件",
    "价值投资",
    "可追踪问题",
)


@dataclass
class LLMExtraction:
    metadata: dict[str, Any]
    markdown: str
    raw_response: str


async def extract_wiki_with_llm(
    text: str,
    base_metadata: dict[str, Any],
    source_path: Path,
    workspace_id: UUID | str | None = None,
    owner_user_id: UUID | str | None = None,
) -> LLMExtraction:
    config = LLMFactory.get_config(workspace_id=workspace_id, owner_user_id=owner_user_id)
    prompt = _build_prompt(
        text=text,
        base_metadata=base_metadata,
        source_path=source_path,
        document_char_limit=_document_char_limit(config.provider, config.max_tokens),
    )
    raw_response = await LLMFactory.generate(prompt, response_format="json", workspace_id=workspace_id, owner_user_id=owner_user_id)
    payload = _parse_json_object(raw_response)
    metadata = _normalize_metadata(payload.get("metadata") or payload.get("yaml_meta") or {}, base_metadata, source_path)
    markdown = _extract_markdown(payload)
    repaired = False
    if not _is_structured_markdown(markdown):
        repair_response = await LLMFactory.generate(
            _build_repair_prompt(
                text=text,
                base_metadata=metadata,
                source_path=source_path,
                document_char_limit=_document_char_limit(config.provider, config.max_tokens),
            ),
            response_format="json",
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
        )
        repair_payload = _parse_json_object(repair_response)
        repair_markdown = _extract_markdown(repair_payload)
        if not _is_structured_markdown(repair_markdown):
            raise ValueError("LLM response did not include a structured Wiki content_md")
        metadata = _normalize_metadata(repair_payload.get("metadata") or {}, metadata, source_path)
        markdown = repair_markdown
        raw_response = f"{raw_response}\n\n--- repair response ---\n\n{repair_response}"
        repaired = True
    if not markdown.startswith("#"):
        markdown = f"# {metadata['title']}\n\n{markdown}"
    metadata["llm_extraction"] = {
        "status": "completed",
        "provider": config.provider,
        "endpoint": config.effective_endpoint,
        "model": config.model_name,
        "response_chars": len(raw_response),
        "repaired": repaired,
    }
    return LLMExtraction(metadata=metadata, markdown=markdown, raw_response=raw_response)


def _build_prompt(text: str, base_metadata: dict[str, Any], source_path: Path, document_char_limit: int = DOCUMENT_CHAR_LIMIT) -> str:
    document = _prepare_document_text(text, document_char_limit)
    metadata_json = json.dumps(base_metadata, ensure_ascii=False, default=str)
    return f"""你是 A 股价值投资导向的年报 / Wiki 分析助手。
请基于给定文档生成结构化 Wiki 页面。必须只输出一个合法 JSON 对象，不要输出 Markdown 代码围栏，不要输出解释文字。

JSON Schema:
{{
  "metadata": {{
    "title": "公司简称 + 年份 + 文档类型，例如：用友网络2024年年报解析",
    "type": "annual-report | general-doc | company-profile | financial-trend | segment-analysis | personnel-profile | risk-event | news-fragment | investment-insight | index",
    "ticker": "SH600588 或 null",
    "report_year": 2024 或 null,
    "publish_date": "YYYY-MM-DD",
    "tags": ["标签"],
    "related": ["双向链接标题"],
    "credibility_score": 0.0,
    "company_name": "公司全称或 null",
    "company_short_name": "公司简称或 null",
    "key_metrics": {{}},
    "business_segments": [],
    "risks": [],
    "executives": [{{"name": "姓名", "role": "职务", "description": "原文依据或简短说明"}}],
    "management_strategy": [],
    "investment_view": []
  }},
  "content_md": "# 标题\\n\\n## 投资摘要\\n...\\n"
}}

正文必须使用中文 Markdown，并至少包含这些章节：
1. 投资摘要
2. 公司与报告概览
3. 财务表现与质量观察
4. 分业务 / 产品趋势
5. 管理层战略与年度目标
6. 风险事件与治理问题
7. 价值投资关注点
8. 可追踪问题

要求：
- 不要把 PDF 页码标记、目录、免责声明当成标题。
- 如果信息缺失，写“未在当前文档中充分披露”，不要编造。
- 使用 [[公司名称]]、[[风险主题]] 等双向链接格式。
- 尽量抽取营收、利润、资产、负债、权益、现金流、分红、股本、业务板块等指标。
- 抽取公司高管/关键联系人到 executives，至少包括董事长、总经理/总裁、财务负责人、董事会秘书、证券事务代表等能在原文中找到的人物；不要编造。
- business_segments、risks、management_strategy、investment_view、executives 中的对象必须带 name/title 或 description，方便后端生成独立 Wiki 词条。
- 如果文档不是年报，也按最接近的类型归档。

已知基础元数据：
{metadata_json}

源文件：
{source_path.name}

文档正文：
{document}
"""


def _build_repair_prompt(
    text: str,
    base_metadata: dict[str, Any],
    source_path: Path,
    document_char_limit: int = DOCUMENT_CHAR_LIMIT,
) -> str:
    document = _prepare_document_text(text, min(document_char_limit, 36000))
    metadata_json = json.dumps(base_metadata, ensure_ascii=False, default=str)
    return f"""上一轮输出没有生成可用的结构化 Wiki 正文。请重新基于文档生成 JSON，必须只输出一个合法 JSON 对象。

硬性要求：
1. 顶层必须包含 metadata 和 content_md。
2. content_md 必须是中文 Markdown 字符串，不能是对象、数组或原文复制。
3. content_md 必须包含这些二级标题：
   ## 投资摘要
   ## 公司与报告概览
   ## 财务表现与质量观察
   ## 分业务 / 产品趋势
   ## 管理层战略与年度目标
   ## 风险事件与治理问题
   ## 价值投资关注点
   ## 可追踪问题
4. 每个章节写成研究员可直接阅读的归纳内容；不要输出“原文摘录”章节。
5. 使用 [[公司名称]]、[[风险主题]]、[[业务板块]] 形式的双向链接。

已知元数据：
{metadata_json}

源文件：
{source_path.name}

文档正文：
{document}
"""


def _document_char_limit(provider: str, max_tokens: int) -> int:
    if provider != "ollama":
        return DOCUMENT_CHAR_LIMIT
    return max(3000, min(DOCUMENT_CHAR_LIMIT, int(max_tokens * 0.6)))


def _prepare_document_text(text: str, char_limit: int = DOCUMENT_CHAR_LIMIT) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(clean) <= char_limit:
        return clean
    head = clean[: int(char_limit * 0.7)].rstrip()
    tail = clean[-int(char_limit * 0.3) :].lstrip()
    return f"{head}\n\n...[中间内容已截断以适配上下文窗口]...\n\n{tail}"


def _parse_json_object(raw_response: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL | re.IGNORECASE).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("LLM response did not contain a valid JSON object")


def _extract_markdown(payload: dict[str, Any]) -> str:
    for key in ("content_md", "markdown", "wiki_markdown", "wiki_page", "content"):
        markdown = _stringify_markdown_value(payload.get(key))
        if markdown:
            return markdown
    sections = payload.get("sections")
    if isinstance(sections, dict):
        title = _payload_title(payload)
        body = "\n\n".join(f"## {heading}\n{_stringify_markdown_value(content)}" for heading, content in sections.items())
        return f"# {title}\n\n{body}".strip()
    if isinstance(sections, list):
        title = _payload_title(payload)
        chunks: list[str] = []
        for item in sections:
            if not isinstance(item, dict):
                continue
            heading = item.get("heading") or item.get("title") or item.get("name")
            content = item.get("content") or item.get("body") or item.get("text")
            if heading and content:
                chunks.append(f"## {heading}\n{_stringify_markdown_value(content)}")
        if chunks:
            return f"# {title}\n\n" + "\n\n".join(chunks)
    return ""


def _stringify_markdown_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        chunks = []
        for heading, content in value.items():
            text = _stringify_markdown_value(content)
            if text:
                chunks.append(f"## {heading}\n{text}")
        return "\n\n".join(chunks).strip()
    if isinstance(value, list):
        chunks = [_stringify_markdown_value(item) for item in value]
        return "\n\n".join(chunk for chunk in chunks if chunk).strip()
    return ""


def _is_structured_markdown(markdown: str) -> bool:
    if not markdown.strip():
        return False
    headings = re.findall(r"^##\s+(.+)$", markdown, flags=re.MULTILINE)
    matched = sum(1 for hint in REQUIRED_SECTION_HINTS if any(hint in heading for heading in headings))
    if matched < 4:
        return False
    if "## 原文摘录" in markdown and "<!-- page:" in markdown[:2000]:
        return False
    return True


def _payload_title(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata.get("title"):
        return str(metadata["title"])
    if payload.get("title"):
        return str(payload["title"])
    return "未命名文档解析"


def _normalize_metadata(llm_metadata: dict[str, Any], base_metadata: dict[str, Any], source_path: Path) -> dict[str, Any]:
    today = date.today().isoformat()
    metadata = {**base_metadata, **{key: value for key, value in llm_metadata.items() if value not in ("", None)}}
    metadata["title"] = _clean_title(str(metadata.get("title") or base_metadata.get("title") or source_path.stem))
    metadata["type"] = metadata.get("type") or base_metadata.get("type") or "general-doc"
    metadata["source_file"] = str(source_path)
    metadata["raw_content_ref"] = str(source_path)
    metadata["created"] = metadata.get("created") or today
    metadata["updated"] = today
    metadata["analysis_status"] = "parsed"
    metadata["credibility_score"] = _safe_float(metadata.get("credibility_score"), 0.78)
    metadata["tags"] = _ensure_list(metadata.get("tags")) or _ensure_list(base_metadata.get("tags"))
    metadata["related"] = _ensure_list(metadata.get("related"))
    metadata["financial_reconciliation"] = run_financial_reconciliation("", metadata, str(metadata["type"]))
    metadata["parse_quality"] = metadata["financial_reconciliation"]["status"]
    return metadata


def _clean_title(title: str) -> str:
    title = re.sub(r"<!--\s*page:\d+\s*-->", "", title).strip(" #\t\r\n")
    return title[:120] or "未命名文档解析"


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
