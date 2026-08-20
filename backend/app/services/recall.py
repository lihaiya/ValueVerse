import re
import json
from collections import Counter
from typing import Any
from uuid import UUID

from sqlmodel import Session, col, select

from app.models import WikiNode
from app.schemas import Citation, RecallRequest, RecallResponse
from app.services.llm_factory import LLMFactory
from app.services.memory import MemoryClient
from app.services.web_search import WebSearchClient


IMPORTANT_CHINESE_TERMS = (
    "用友网络",
    "年报",
    "年度报告",
    "营收",
    "收入",
    "营业收入",
    "利润",
    "净利润",
    "现金流",
    "自由现金流",
    "毛利率",
    "资产",
    "负债",
    "股东权益",
    "风险",
    "诉讼",
    "合规",
    "战略",
    "目标",
    "管理层",
    "董事长",
    "业务",
    "产品",
    "分红",
)


async def build_recall_response(
    session: Session,
    request: RecallRequest,
    memory_client: MemoryClient,
    workspace_id: UUID | str | None = None,
    owner_user_id: UUID | str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> RecallResponse:
    filters = {**request.filters}
    if workspace_id is not None:
        filters["workspace_id"] = str(workspace_id)
    if owner_user_id is not None:
        filters["owner_user_id"] = str(owner_user_id)
    memory_result = await memory_client.recall(request.query, request.top_k, filters)
    local_hits = _local_search(session, request.query, request.top_k, filters)
    web_result = None
    web_error = None
    if request.use_web_search:
        try:
            web_result = await WebSearchClient(workspace_id=workspace_id).search(request.query, top_k=min(request.top_k, 5))
        except Exception as exc:
            web_error = str(exc)
    citations = [
        Citation(node_id=node.id, title=node.title, score=score, link=f"[[{node.title}]]")
        for node, score in local_hits
    ]
    if web_result is not None:
        citations.extend(
            Citation(node_id=None, title=item.title, score=0.4, link=item.url or item.title)
            for item in web_result.results[:5]
        )
    context = _build_context(local_hits, memory_result, request.query, web_result.results if web_result else [])
    strong_match = bool(local_hits and max(score for _, score in local_hits) >= 0.15)
    if local_hits:
        if not strong_match:
            fallback_answer = f"当前 Wiki 中没有找到强匹配条目，但已有可参考材料：\n{_local_context(local_hits, request.query)}"
            confidence = 0.35
        else:
            fallback_answer = f"基于当前 Wiki，最相关的材料如下：\n{_local_context(local_hits, request.query)}"
            confidence = min(0.95, 0.45 + 0.1 * len(local_hits))
    else:
        fallback_answer = "当前业务 Wiki 未检索到足够相关的条目。请先上传年报、公告或研究材料。"
        confidence = 0.25
    answer = fallback_answer
    llm_used = False
    try:
        prompt_context = context
        conversation_context = _conversation_context(conversation_history or [])
        if conversation_context:
            prompt_context = f"## Recent conversation\n{conversation_context}\n\n{context}"
        answer = _clean_llm_answer(
            await LLMFactory.generate(
                _build_answer_prompt(request.query, prompt_context, strong_match),
                response_format=None,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
            )
        )
        answer = answer.strip() or fallback_answer
        llm_used = True
        if context:
            confidence = max(confidence, 0.55 if strong_match else 0.4)
    except Exception:
        answer = f"{fallback_answer}\n\n（LLM 生成暂不可用，已返回本地检索摘要。）"
    backend_parts = [memory_result.get("backend", "local-fallback"), "local", "llm" if llm_used else "llm-fallback"]
    if web_result is not None:
        backend_parts.append("web")
    elif web_error:
        backend_parts.append("web-fallback")
    return RecallResponse(
        answer=answer,
        citations=citations,
        confidence=confidence,
        memory_backend="+".join(backend_parts),
    )


def _build_context(local_hits: list[tuple[WikiNode, float]], memory_result: dict[str, Any], query: str, web_results: list[object] | None = None) -> str:
    chunks = []
    local_context = _local_context(local_hits, query)
    if local_context:
        chunks.append(f"## 本地 Wiki 召回\n{local_context}")
    memory_context = _memory_context(memory_result)
    if memory_context:
        chunks.append(f"## Cognee 召回\n{memory_context}")
    web_context = _web_context(web_results or [])
    if web_context:
        chunks.append(f"## 外部 Web Search\n{web_context}")
    return "\n\n".join(chunks)[:16000]


def _local_context(local_hits: list[tuple[WikiNode, float]], query: str) -> str:
    return "\n".join(f"- [[{node.title}]]: {_snippet(node.content_md or '', query)}" for node, _ in local_hits)


def _memory_context(memory_result: dict[str, Any]) -> str:
    items = memory_result.get("items")
    if not items:
        return ""
    if not isinstance(items, list):
        items = [items]
    chunks: list[str] = []
    for item in items[:8]:
        text = _memory_item_text(item)
        if text:
            chunks.append(f"- {text[:800]}")
    return "\n".join(chunks)


def _web_context(web_results: list[object]) -> str:
    chunks: list[str] = []
    for item in web_results[:5]:
        title = getattr(item, "title", "")
        url = getattr(item, "url", None)
        snippet = getattr(item, "snippet", "")
        if title or snippet:
            suffix = f" ({url})" if url else ""
            chunks.append(f"- {title}{suffix}: {snippet[:800]}")
    return "\n".join(chunks)


def _memory_item_text(item: object) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("text", "content", "chunk", "summary", "value"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(item, ensure_ascii=False, default=str)
    return str(item).strip()


def _conversation_context(messages: list[dict[str, str]], max_chars: int = 12000) -> str:
    lines: list[str] = []
    used = 0
    for message in reversed(messages):
        role = str(message.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        line = f"{role}: {content}"
        remaining = max_chars - used
        if remaining <= 0:
            break
        lines.append(line[-remaining:])
        used += min(len(line), remaining)
    return "\n".join(reversed(lines))


def _build_answer_prompt(query: str, context: str, strong_match: bool) -> str:
    evidence_note = "有本地 Wiki 证据，请优先依据证据回答。" if strong_match else "本地 Wiki 证据不足或匹配较弱。"
    safe_context = context or "无本地 Wiki / Cognee 召回内容。"
    return f"""你是面向价值投资研究员的 AI 研究助手。

回答规则：
- {evidence_note}
- 能引用本地 Wiki 条目时，使用 [[条目标题]] 格式。
- 如果本地证据不足，可以基于通用金融和商业分析知识给出研究框架、待核验假设和下一步资料需求。
- 如果提供了外部 Web Search，上网结果只能作为待核验外部资料；涉及公司事实、履历、公告日期时说明来源或提示需要核验。
- 不要编造当前 Wiki 中没有证据支撑的具体财务数字、公告结论或公司事实。
- 输出中文 Markdown，结构清晰，避免只复述材料列表。

用户问题：
{query}

可用上下文：
{safe_context}
"""


def _clean_llm_answer(answer: str) -> str:
    return re.sub(r"<think>.*?</think>\s*", "", answer or "", flags=re.DOTALL | re.IGNORECASE).strip()


def _local_search(session: Session, query: str, top_k: int, filters: dict[str, object]) -> list[tuple[WikiNode, float]]:
    stmt = select(WikiNode).order_by(col(WikiNode.updated_at).desc())
    if filters.get("workspace_id"):
        stmt = stmt.where(WikiNode.workspace_id == UUID(str(filters["workspace_id"])))
    if filters.get("type"):
        stmt = stmt.where(WikiNode.type == str(filters["type"]))
    nodes = [node for node in session.exec(stmt).all() if not _is_hidden_node(node)]
    query_terms = _terms(query)
    ranked: list[tuple[WikiNode, float]] = []
    for node in nodes:
        if not _matches_filters(node, filters):
            continue
        haystack = _node_haystack(node)
        score = _score(query_terms, haystack)
        score += _metadata_boost(query, query_terms, node)
        if score > 0:
            ranked.append((node, min(1.0, score)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    if ranked:
        return ranked[:top_k]
    return [(node, 0.05) for node in nodes[:top_k] if _matches_filters(node, filters)]


def _terms(text: str) -> list[str]:
    normalized = text.lower()
    terms: list[str] = []
    terms.extend(re.findall(r"[a-zA-Z]{2,}|\d{4}|\d{6}", normalized))
    for term in IMPORTANT_CHINESE_TERMS:
        if term in text:
            terms.append(term)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if len(chunk) <= 8:
            terms.append(chunk)
        for size in (2, 3, 4):
            terms.extend(chunk[index : index + size] for index in range(0, max(0, len(chunk) - size + 1)))
    return list(dict.fromkeys(term for term in terms if term and term not in {"什么", "哪些", "如何", "怎么样", "请问"}))


def _score(query_terms: list[str], text: str) -> float:
    if not query_terms:
        return 0.0
    lowered = text.lower()
    counts = Counter(term for term in query_terms if term.lower() in lowered)
    if not counts:
        return 0.0
    raw = 0.0
    for term, count in counts.items():
        weight = 1.6 if re.fullmatch(r"\d{4}|\d{6}", term) else 1.0
        if len(term) >= 4:
            weight += 0.4
        raw += count * weight
    return min(0.85, raw / max(5, len(query_terms)))


def _node_haystack(node: WikiNode) -> str:
    metadata = json.dumps(node.yaml_meta or {}, ensure_ascii=False, default=str)
    return f"{node.title}\n{node.type}\n{metadata}\n{node.content_md or ''}"


def _is_hidden_node(node: WikiNode) -> bool:
    status = str((node.yaml_meta or {}).get("analysis_status") or "").strip().lower()
    return status in {"deprecated", "deleted"}


def _metadata_boost(query: str, query_terms: list[str], node: WikiNode) -> float:
    metadata: dict[str, Any] = node.yaml_meta or {}
    boost = 0.0
    title = node.title.lower()
    if any(term.lower() in title for term in query_terms):
        boost += 0.12
    if node.type == "company-profile" and node.title and node.title in query:
        boost += 0.45
    year = metadata.get("report_year")
    if year and str(year) in query:
        boost += 0.18
    ticker = metadata.get("ticker")
    if ticker and str(ticker).lower() in query.lower():
        boost += 0.18
    company_names = [metadata.get("company_name"), metadata.get("company_short_name")]
    if any(name and str(name) in query for name in company_names):
        boost += 0.36 if node.type == "company-profile" else 0.18
    tags = metadata.get("tags")
    if isinstance(tags, list) and any(str(tag) in query for tag in tags):
        boost += 0.08
    return boost


def _matches_filters(node: WikiNode, filters: dict[str, object]) -> bool:
    if not filters:
        return True
    metadata = node.yaml_meta or {}
    for key in ("ticker", "report_year", "company_short_name", "company_name", "folder_path"):
        expected = filters.get(key)
        if expected is not None and expected != "" and str(metadata.get(key) or "") != str(expected):
            return False
    return True


def _snippet(text: str, query: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""
    for term in _terms(query):
        index = compact.lower().find(term.lower())
        if index >= 0:
            start = max(0, index - 80)
            end = min(len(compact), index + 180)
            return compact[start:end]
    return compact[:240]
