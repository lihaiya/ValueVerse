from base64 import b64encode
import re
from time import perf_counter
from typing import Annotated, Callable
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.auth import WorkspaceContextDep, get_workspace_context, require_owner_or_admin
from app.core.config import get_settings
from app.core.secrets import decrypt_api_key, encrypt_api_key
from app.db.session import get_session
from app.models import AuditLog, ChatConversation, ChatMessage, Domain, DomainPack, DomainPackBinding, EvidenceLink, LLMConfigTable, ParseStatus, ParseTask, ParsedArtifact, SourceDocument, SourceSpan, WebSearchConfigTable, WikiNode, utcnow
from app.models import KnowledgeEdge
from app.schemas import (
    ChatConversationRead,
    ChatConversationUpdate,
    ChatMessageRead,
    ClearKnowledgeRequest,
    ClearKnowledgeResponse,
    DialogRequest,
    DomainCreate,
    DomainPackRead,
    DomainPackCreate,
    DomainPackUpdate,
    DomainRead,
    DomainUpdate,
    EvidenceRead,
    ForgetRequest,
    GraphEdge,
    GraphNode,
    GraphResponse,
    ImproveRequest,
    LLMConfigCreate,
    LLMConfigRead,
    LLMTestResponse,
    LLMConfigUpdate,
    OperationResponse,
    ParseTaskRead,
    RawContentRead,
    RecallRequest,
    RecallResponse,
    ScoringRequest,
    ScoringResponse,
    SourceDocumentRead,
    SourceSpanRead,
    WebSearchConfigCreate,
    WebSearchConfigRead,
    WebSearchConfigUpdate,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchTestResponse,
    WikiWebEnrichRequest,
    WikiNodeListItem,
    WikiNodeRead,
)
from app.services.document_pipeline import process_staged_upload, stage_upload
from app.services.knowledge_delete import collect_source_document_related, delete_local_source_files, delete_source_document_records
from app.services.llm_factory import LLMFactory, RuntimeLLMConfig
from app.services.memory import MemoryClient
from app.services.raw_content import load_raw_content
from app.services.recall import build_recall_response
from app.services.scoring import evaluate_value_score
from app.services.web_search import WebSearchClient

public_router = APIRouter()
router = APIRouter(dependencies=[Depends(get_workspace_context)])
SessionDep = Annotated[Session, Depends(get_session)]


def _scope_record(record: object, context: WorkspaceContextDep) -> None:
    if hasattr(record, "workspace_id"):
        setattr(record, "workspace_id", context.workspace_id)
    if hasattr(record, "owner_user_id"):
        setattr(record, "owner_user_id", context.user_id)


def _is_workspace_record(record: object | None, context: WorkspaceContextDep) -> bool:
    return bool(record is not None and getattr(record, "workspace_id", None) == context.workspace_id)


def _is_system_record(record: object | None) -> bool:
    return bool(record is not None and getattr(record, "workspace_id", None) is None and getattr(record, "owner_type", None) == "system")


def _ensure_workspace_record(record: object | None, context: WorkspaceContextDep, detail: str) -> None:
    if not _is_workspace_record(record, context):
        raise HTTPException(status_code=404, detail=detail)


def _workspace_filter(model: type, context: WorkspaceContextDep):
    return getattr(model, "workspace_id") == context.workspace_id


def _workspace_or_system_filter(model: type, context: WorkspaceContextDep):
    return or_(getattr(model, "workspace_id") == context.workspace_id, getattr(model, "workspace_id").is_(None))


def _actor(context: WorkspaceContextDep) -> str:
    return context.user.email[:50]


@public_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/docs/upload", response_model=ParseTaskRead)
async def upload_document(
    session: SessionDep,
    context: WorkspaceContextDep,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    domain_id: UUID | None = Form(default=None),
    domain_pack_ids: list[UUID] | None = Form(default=None),
    folder_path: str | None = Form(default=None),
) -> ParseTask:
    task = ParseTask(filename=file.filename or "upload", status=ParseStatus.pending, progress=0)
    _scope_record(task, context)
    session.add(task)
    session.commit()
    session.refresh(task)
    source_document = await stage_upload(
        file=file,
        task=task,
        session=session,
        selected_domain_id=domain_id,
        selected_domain_pack_ids=domain_pack_ids or [],
        folder_path=folder_path,
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
    )
    background_tasks.add_task(
        process_staged_upload,
        task.id,
        source_document.id,
        domain_id,
        domain_pack_ids or [],
        folder_path,
        context.workspace_id,
        context.user_id,
    )
    session.refresh(task)
    return task


@router.get("/parse/status/{task_id}", response_model=ParseTaskRead)
def parse_status(task_id: UUID, session: SessionDep, context: WorkspaceContextDep) -> ParseTask:
    task = session.get(ParseTask, task_id)
    if task is None or not _is_workspace_record(task, context):
        raise HTTPException(status_code=404, detail="parse task not found")
    return task


@router.get("/wiki/nodes", response_model=list[WikiNodeListItem])
def list_wiki_nodes(session: SessionDep, context: WorkspaceContextDep, limit: int = 50, q: str | None = None) -> list[WikiNodeListItem]:
    stmt = select(WikiNode).where(_workspace_filter(WikiNode, context)).order_by(col(WikiNode.updated_at).desc()).limit(min(limit, 1000))
    if q:
        like = f"%{q}%"
        stmt = (
            select(WikiNode)
            .where(_workspace_filter(WikiNode, context), WikiNode.title.like(like))
            .order_by(col(WikiNode.updated_at).desc())
            .limit(min(limit, 1000))
        )
    nodes = [node for node in session.exec(stmt).all() if _is_visible_wiki_node(node)]
    return [
        WikiNodeListItem(
            id=node.id,
            title=node.title,
            type=node.type,
            aliases=_node_aliases(node),
            analysis_status=node.yaml_meta.get("analysis_status"),
            credibility_score=node.yaml_meta.get("credibility_score"),
            cognee_doc_hash=node.cognee_doc_hash,
            updated_at=node.updated_at,
        )
        for node in nodes
    ]


def _node_aliases(node: WikiNode) -> list[str]:
    metadata = node.yaml_meta or {}
    raw_aliases = [
        metadata.get("company_name"),
        metadata.get("company_short_name"),
        metadata.get("ticker"),
        *(metadata.get("aliases") if isinstance(metadata.get("aliases"), list) else []),
    ]
    aliases: list[str] = []
    for value in raw_aliases:
        clean = str(value or "").strip()
        if clean and clean != node.title and clean not in aliases:
            aliases.append(clean)
    return aliases


def _is_visible_wiki_node(node: WikiNode) -> bool:
    status = str((node.yaml_meta or {}).get("analysis_status") or "").strip().lower()
    return status not in {"deprecated", "deleted"}


@router.get("/wiki/node/{node_id}", response_model=WikiNodeRead)
def get_wiki_node(node_id: UUID, session: SessionDep, context: WorkspaceContextDep) -> WikiNodeRead:
    node = session.get(WikiNode, node_id)
    if node is None or not _is_workspace_record(node, context) or not _is_visible_wiki_node(node):
        raise HTTPException(status_code=404, detail="wiki node not found")
    return _wiki_node_to_read(session, node, context)


@router.post("/wiki/node/{node_id}/web-enrich", response_model=WikiNodeRead)
async def web_enrich_wiki_node(
    node_id: UUID,
    session: SessionDep,
    context: WorkspaceContextDep,
    request: WikiWebEnrichRequest | None = None,
) -> WikiNodeRead:
    node = session.get(WikiNode, node_id)
    if node is None or not _is_workspace_record(node, context) or not _is_visible_wiki_node(node):
        raise HTTPException(status_code=404, detail="wiki node not found")
    _normalize_node_title_for_enrichment(session, node, context)
    payload = request or WikiWebEnrichRequest()
    query = _web_enrich_query(session, node, payload.query, context)
    try:
        web_result = await WebSearchClient(workspace_id=context.workspace_id).search(query, top_k=payload.top_k)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"web search failed: {exc}") from exc
    if not web_result.results:
        raise HTTPException(status_code=404, detail="web search returned no results")

    old_content = node.content_md or ""
    prompt = _build_web_enrich_prompt(node, old_content, web_result.results)
    try:
        generated = _clean_generated_markdown(
            await LLMFactory.generate(
                prompt,
                response_format=None,
                workspace_id=context.workspace_id,
                owner_user_id=context.user_id,
            )
        )
        new_content = _finalize_web_enrichment_content(node, generated, old_content, web_result.results)
    except Exception:
        new_content = _fallback_web_enrichment_content(node, old_content, web_result.results)

    external_sources = [
        {"title": item.title, "url": item.url, "snippet": item.snippet[:500]}
        for item in web_result.results
    ]
    metadata = dict(node.yaml_meta or {})
    metadata["analysis_status"] = "parsed"
    metadata["description"] = _enriched_description(metadata.get("description"), web_result.results)
    metadata["web_enrichment"] = {
        "query": query,
        "provider": web_result.provider,
        "updated_at": utcnow().isoformat(),
        "result_count": len(web_result.results),
    }
    metadata["external_sources"] = external_sources
    node.yaml_meta = metadata
    node.content_md = new_content
    node.updated_at = utcnow()
    session.add(node)
    session.add(
        AuditLog(
            workspace_id=context.workspace_id,
            owner_user_id=context.user_id,
            action="web_enrich_wiki_node",
            target_type="wiki_node",
            target_id=str(node.id),
            payload={"query": query, "provider": web_result.provider, "sources": external_sources},
            created_by=_actor(context),
        )
    )
    session.commit()
    session.refresh(node)
    return _wiki_node_to_read(session, node, context)


def _wiki_node_to_read(session: Session, node: WikiNode, context: WorkspaceContextDep) -> WikiNodeRead:
    return WikiNodeRead(
        id=node.id,
        title=node.title,
        type=node.type,
        yaml_meta=node.yaml_meta,
        content_md=node.content_md,
        raw_content_ref=node.raw_content_ref,
        cognee_doc_hash=node.cognee_doc_hash,
        created_at=node.created_at,
        updated_at=node.updated_at,
        related_nodes=_related_nodes_for(session, node, context),
    )


def _normalize_node_title_for_enrichment(session: Session, node: WikiNode, context: WorkspaceContextDep) -> None:
    normalized = _normalize_wiki_title(node.title)
    if normalized == node.title:
        return
    existing = session.exec(
        select(WikiNode).where(_workspace_filter(WikiNode, context), WikiNode.title == normalized, WikiNode.type == node.type)
    ).first()
    if existing is not None and existing.id != node.id:
        return
    metadata = dict(node.yaml_meta or {})
    metadata["title"] = normalized
    node.title = normalized
    node.yaml_meta = metadata
    if node.content_md:
        node.content_md = re.sub(r"^#\s+.+$", f"# {normalized}", node.content_md, count=1, flags=re.MULTILINE)
    node.updated_at = utcnow()
    session.add(node)


def _normalize_wiki_title(title: str) -> str:
    text = str(title or "").strip()
    match = re.fullmatch(r"\[\s*['\"]([^'\"]{1,120})['\"]\s*]", text)
    if match:
        return match.group(1).strip()
    return text


def _web_enrich_query(session: Session, node: WikiNode, override: str | None, context: WorkspaceContextDep) -> str:
    if override and override.strip():
        return override.strip()
    metadata = node.yaml_meta or {}
    context_terms = _web_enrich_context_terms(session, node, context)
    terms = [
        node.title,
        *context_terms,
        metadata.get("role"),
        metadata.get("position"),
        _web_enrich_type_hint(node.type),
    ]
    unique_terms: list[str] = []
    for term in terms:
        clean = str(term or "").strip()
        if clean and clean not in unique_terms:
            unique_terms.append(clean)
    return " ".join(unique_terms)[:300]


def _web_enrich_context_terms(session: Session, node: WikiNode, context: WorkspaceContextDep) -> list[str]:
    metadata = node.yaml_meta or {}
    terms: list[object] = [
        metadata.get("company_short_name"),
        metadata.get("company_name"),
        metadata.get("ticker"),
        _normalize_ticker(metadata.get("ticker")) if metadata.get("ticker") else None,
    ]
    if metadata.get("company_short_name") or metadata.get("company_name"):
        return [str(term).strip() for term in terms if str(term or "").strip()]

    related_ids = [
        edge.src_node_id if edge.tgt_node_id == node.id else edge.tgt_node_id
        for edge in session.exec(
            select(KnowledgeEdge).where(
                _workspace_filter(KnowledgeEdge, context),
                (KnowledgeEdge.src_node_id == node.id) | (KnowledgeEdge.tgt_node_id == node.id),
            )
        ).all()
    ]
    for related_id in related_ids:
        related = session.get(WikiNode, related_id)
        if related is None or not _is_workspace_record(related, context):
            continue
        related_meta = related.yaml_meta or {}
        if related.type in {"company-profile", "annual-report", "company-overview"}:
            terms.extend(
                [
                    related.title,
                    related_meta.get("company_short_name"),
                    related_meta.get("company_name"),
                    related_meta.get("ticker"),
                    _normalize_ticker(related_meta.get("ticker")) if related_meta.get("ticker") else None,
                ]
            )
            break
    return [str(term).strip() for term in terms if str(term or "").strip()]


def _web_enrich_type_hint(node_type: str) -> str:
    if node_type == "company-executive-profile":
        return "A股 上市公司 高管 履历 官方"
    if node_type.startswith("company-risk"):
        return "A股 上市公司 风险 公告 官方"
    if node_type in {"company-finance-segment", "investment-insight"}:
        return "A股 上市公司 业务 财务 年报 官方"
    return "概念 介绍 官方"


def _build_web_enrich_prompt(node: WikiNode, old_content: str, results: list[object]) -> str:
    metadata = node.yaml_meta or {}
    result_text = _format_web_results(results)
    return f"""你是面向价值投资研究员的 Wiki 编辑助手。请基于当前词条和联网搜索结果，更新为一篇更完整的中文 Markdown 词条。

硬性规则：
- 只能使用“当前词条”和“联网搜索结果”中的信息，不要编造。
- 如果搜索结果不足以确认某事实，写成“待进一步核验”，不要下结论。
- 保留一级标题 "# {node.title}"。
- 对概念/业务/人物都要给出可读介绍，并补充“投资研究关注点”。
- 末尾必须有“## 联网来源”，逐条列出来源标题和 URL；没有 URL 时写来源标题。
- 不要输出 JSON，不要输出思考过程。

当前词条：
title: {node.title}
type: {node.type}
company: {metadata.get("company_short_name") or metadata.get("company_name") or "未归属"}
metadata description: {metadata.get("description") or ""}

当前 Markdown：
{old_content[:6000]}

联网搜索结果：
{result_text}
"""


def _format_web_results(results: list[object]) -> str:
    lines: list[str] = []
    for index, item in enumerate(results[:8], start=1):
        title = str(getattr(item, "title", "") or "")
        url = str(getattr(item, "url", "") or "")
        snippet = str(getattr(item, "snippet", "") or "")
        lines.append(f"{index}. {title}\nURL: {url or 'N/A'}\n摘要: {snippet[:1200]}")
    return "\n\n".join(lines)


def _clean_generated_markdown(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE).strip()
    match = re.search(r"```(?:markdown|md)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if match:
        cleaned = match.group(1).strip()
    return cleaned


def _finalize_web_enrichment_content(node: WikiNode, generated: str, old_content: str, results: list[object]) -> str:
    if not generated.strip():
        return _fallback_web_enrichment_content(node, old_content, results)
    if not generated.lstrip().startswith("#"):
        return f"# {node.title}\n\n{generated.strip()}"
    return generated.strip()


def _fallback_web_enrichment_content(node: WikiNode, old_content: str, results: list[object]) -> str:
    base = old_content.strip() or f"# {node.title}\n\n## 概念说明\n该词条由知识库材料生成，仍需补充。"
    source_lines = []
    for item in results[:5]:
        title = str(getattr(item, "title", "") or "联网搜索结果")
        url = str(getattr(item, "url", "") or "")
        snippet = str(getattr(item, "snippet", "") or "")
        link = f"[{title}]({url})" if url else title
        source_lines.append(f"- {link}：{snippet[:300]}")
    section = "\n\n## 联网补充材料\n以下内容来自联网搜索，尚需人工核验后沉淀为正式定义：\n" + "\n".join(source_lines)
    if "## 联网补充材料" in base or "## 联网来源" in base:
        return base
    return f"{base}{section}"


def _enriched_description(current: object, results: list[object]) -> str:
    current_text = str(current or "").strip()
    if current_text and current_text not in {"正文双链", "相关概念", "概念"} and len(current_text) >= 24:
        return current_text
    for item in results:
        snippet = str(getattr(item, "snippet", "") or "").strip()
        if snippet:
            return snippet[:180]
    return current_text or "已通过联网搜索补充，待人工核验。"


@router.get("/wiki/raw-content/{node_id}", response_model=RawContentRead)
def get_raw_content(node_id: UUID, session: SessionDep, context: WorkspaceContextDep) -> RawContentRead:
    node = session.get(WikiNode, node_id)
    if node is None or not _is_workspace_record(node, context):
        raise HTTPException(status_code=404, detail="wiki node not found")
    raw_content_ref = _raw_content_ref_for_node(session, node, context)
    if not raw_content_ref:
        raise HTTPException(status_code=404, detail="raw content not available")

    try:
        raw = load_raw_content(raw_content_ref)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if raw.kind == "pdf":
        return RawContentRead(
            node_id=node.id,
            filename=raw.filename,
            kind=raw.kind,
            mime_type=raw.mime_type,
            base64=b64encode(raw.bytes_content).decode("ascii"),
        )
    return RawContentRead(
        node_id=node.id,
        filename=raw.filename,
        kind=raw.kind,
        mime_type=raw.mime_type,
        text=raw.text,
    )


def _raw_content_ref_for_node(session: Session, node: WikiNode, context: WorkspaceContextDep) -> str | None:
    if node.raw_content_ref:
        return node.raw_content_ref
    metadata = node.yaml_meta or {}
    source_ids: list[UUID] = []
    direct_source_id = _uuid_or_none(metadata.get("source_document_id"))
    if direct_source_id is not None:
        source_ids.append(direct_source_id)
    source_ids.extend(_uuid_list(metadata.get("source_document_ids")))
    for source_id in source_ids:
        source_document = session.get(SourceDocument, source_id)
        if source_document is not None and _is_workspace_record(source_document, context) and source_document.storage_uri:
            return source_document.storage_uri

    links = session.exec(
        select(EvidenceLink)
        .where(_workspace_filter(EvidenceLink, context), EvidenceLink.target_type == "wiki_node", EvidenceLink.target_id == str(node.id))
        .order_by(col(EvidenceLink.relevance_score).desc(), col(EvidenceLink.created_at).asc())
        .limit(20)
    ).all()
    for link in links:
        span = session.get(SourceSpan, link.source_span_id)
        if span is None or not _is_workspace_record(span, context):
            continue
        source_document = session.get(SourceDocument, span.source_document_id)
        if source_document is not None and _is_workspace_record(source_document, context) and source_document.storage_uri:
            return source_document.storage_uri
    return None


@router.get("/wiki/node/{node_id}/evidence", response_model=list[EvidenceRead])
def list_wiki_node_evidence(node_id: UUID, session: SessionDep, context: WorkspaceContextDep, limit: int = 50) -> list[EvidenceRead]:
    node = session.get(WikiNode, node_id)
    if node is None or not _is_workspace_record(node, context):
        raise HTTPException(status_code=404, detail="wiki node not found")
    links = session.exec(
        select(EvidenceLink)
        .where(_workspace_filter(EvidenceLink, context), EvidenceLink.target_type == "wiki_node", EvidenceLink.target_id == str(node_id))
        .order_by(col(EvidenceLink.relevance_score).desc(), col(EvidenceLink.created_at).asc())
        .limit(min(limit, 200))
    ).all()
    evidence: list[EvidenceRead] = []
    for link in links:
        span = session.get(SourceSpan, link.source_span_id)
        if span is None or not _is_workspace_record(span, context):
            continue
        evidence.append(
            EvidenceRead(
                id=link.id,
                target_type=link.target_type,
                target_id=link.target_id,
                source_span_id=link.source_span_id,
                quote=link.quote,
                relevance_score=link.relevance_score,
                evidence_metadata=link.evidence_metadata,
                created_at=link.created_at,
                span=SourceSpanRead(
                    id=span.id,
                    source_document_id=span.source_document_id,
                    parsed_artifact_id=span.parsed_artifact_id,
                    span_type=span.span_type,
                    locator=span.locator,
                    text=span.text,
                    char_start=span.char_start,
                    char_end=span.char_end,
                    confidence=span.confidence,
                    created_at=span.created_at,
                ),
            )
        )
    return evidence


@router.get("/sources/documents", response_model=list[SourceDocumentRead])
def list_source_documents(session: SessionDep, context: WorkspaceContextDep, limit: int = 50, folder_path: str | None = None) -> list[SourceDocument]:
    documents = session.exec(
        select(SourceDocument)
        .where(_workspace_filter(SourceDocument, context))
        .order_by(col(SourceDocument.created_at).desc())
        .limit(min(limit, 200))
    ).all()
    _hydrate_source_document_metadata(session, documents, context)
    if folder_path is None:
        return documents
    return [document for document in documents if str(document.document_metadata.get("folder_path") or "") == folder_path]


@router.get("/sources/document/{source_document_id}/spans", response_model=list[SourceSpanRead])
def list_source_spans(source_document_id: UUID, session: SessionDep, context: WorkspaceContextDep, limit: int = 200) -> list[SourceSpan]:
    source_document = session.get(SourceDocument, source_document_id)
    if source_document is None or not _is_workspace_record(source_document, context):
        raise HTTPException(status_code=404, detail="source document not found")
    return session.exec(
        select(SourceSpan)
        .where(_workspace_filter(SourceSpan, context), SourceSpan.source_document_id == source_document_id)
        .order_by(col(SourceSpan.char_start).asc())
        .limit(min(limit, 1000))
    ).all()


@router.post("/sources/document/{source_document_id}/cancel", response_model=OperationResponse)
def cancel_source_document(
    source_document_id: UUID,
    session: SessionDep,
    context: WorkspaceContextDep,
) -> OperationResponse:
    source_document = session.get(SourceDocument, source_document_id)
    if source_document is None or not _is_workspace_record(source_document, context):
        raise HTTPException(status_code=404, detail="source document not found")
    if source_document.status not in {"uploaded", "parsing", "extracting", "cancel_requested"}:
        return OperationResponse(ok=True, message="document is not processing", details={"status": source_document.status})
    task = _source_document_task(session, source_document)
    source_document.status = "cancel_requested"
    source_document.document_metadata = {
        **source_document.document_metadata,
        "analysis_status": "cancel_requested",
        "cancel_requested_at": utcnow().isoformat(),
    }
    source_document.updated_at = utcnow()
    if task is not None:
        task.status = ParseStatus.cancelled
        task.progress = 100
        task.message = "Cancellation requested by user"
        task.updated_at = utcnow()
        session.add(task)
    session.add(source_document)
    session.commit()
    return OperationResponse(ok=True, message="document cancellation requested", details={"status": source_document.status})


@router.delete("/sources/document/{source_document_id}", response_model=OperationResponse)
async def delete_source_document(
    source_document_id: UUID,
    session: SessionDep,
    context: WorkspaceContextDep,
    delete_source_file: bool = False,
) -> OperationResponse:
    source_document = session.get(SourceDocument, source_document_id)
    if source_document is None or not _is_workspace_record(source_document, context):
        raise HTTPException(status_code=404, detail="source document not found")
    if source_document.status in {"parsing", "extracting"}:
        task = _source_document_task(session, source_document)
        source_document.status = "deleting"
        source_document.document_metadata = {
            **source_document.document_metadata,
            "analysis_status": "delete_requested",
            "delete_source_file": delete_source_file,
            "delete_requested_at": utcnow().isoformat(),
        }
        source_document.updated_at = utcnow()
        if task is not None:
            task.status = ParseStatus.cancelled
            task.progress = 100
            task.message = "Deletion requested; processing will stop at the next checkpoint"
            task.updated_at = utcnow()
            session.add(task)
        session.add(source_document)
        session.commit()
        return OperationResponse(ok=True, message="document deletion requested", details={"delete_pending": True})

    memory_result = await MemoryClient(session, owner_user_id=context.user_id).forget(
        workspace_id=context.workspace_id,
        local_resource_type="source_document",
        local_resource_id=str(source_document.id),
    )
    if not memory_result.get("ok"):
        source_document.status = "delete_failed"
        source_document.document_metadata = {**source_document.document_metadata, "memory_delete": memory_result}
        source_document.updated_at = utcnow()
        session.add(source_document)
        session.commit()
        return OperationResponse(ok=False, message="external memory deletion failed", details=memory_result)
    deleted = delete_source_document_records(session, source_document, delete_source_file=delete_source_file)
    session.commit()
    return OperationResponse(
        ok=True,
        message="source document deleted",
        details={**deleted, "memory_forget": memory_result},
    )


@router.post("/sources/document/{source_document_id}/reparse", response_model=ParseTaskRead)
async def reparse_source_document(
    source_document_id: UUID,
    session: SessionDep,
    context: WorkspaceContextDep,
    background_tasks: BackgroundTasks,
) -> ParseTask:
    source_document = session.get(SourceDocument, source_document_id)
    if source_document is None or not _is_workspace_record(source_document, context):
        raise HTTPException(status_code=404, detail="source document not found")
    if source_document.status in {"uploaded", "parsing", "extracting"}:
        raise HTTPException(status_code=409, detail="document is still being processed")

    memory_result = await MemoryClient(session, owner_user_id=context.user_id).forget(
        workspace_id=context.workspace_id,
        local_resource_type="source_document",
        local_resource_id=str(source_document.id),
    )
    if not memory_result.get("ok"):
        raise HTTPException(status_code=502, detail="could not delete the previous external memory before reparse")
    related = _collect_source_document_related(session, source_document)
    _delete_related_records(session, related)
    task = ParseTask(
        filename=source_document.filename,
        status=ParseStatus.pending,
        progress=0,
        raw_content_ref=source_document.storage_uri,
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
    )
    source_document.status = "uploaded"
    source_document.document_metadata = {
        **source_document.document_metadata,
        "analysis_status": "reparse_queued",
        "llm_extraction": None,
        "parse_task_id": str(task.id),
    }
    source_document.updated_at = utcnow()
    session.add(task)
    session.add(source_document)
    session.commit()
    session.refresh(task)

    metadata = source_document.document_metadata or {}
    background_tasks.add_task(
        process_staged_upload,
        task.id,
        source_document.id,
        _uuid_or_none(metadata.get("selected_domain_id")),
        _uuid_list(metadata.get("selected_domain_pack_ids")),
        str(metadata.get("folder_path") or ""),
        context.workspace_id,
        context.user_id,
    )
    return task


def _collect_source_document_related(session: Session, source_document: SourceDocument) -> dict[str, list]:
    return collect_source_document_related(session, source_document)


def _delete_related_records(session: Session, related: dict[str, list]) -> None:
    for key in ("evidence_links", "knowledge_edges", "spans", "artifacts", "tasks", "wiki_nodes"):
        for record in related[key]:
            session.delete(record)
        if related[key]:
            session.flush()


def _uuid_or_none(value: object) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    result: list[UUID] = []
    for item in value:
        parsed = _uuid_or_none(item)
        if parsed is not None:
            result.append(parsed)
    return result


def _source_document_task(session: Session, source_document: SourceDocument) -> ParseTask | None:
    parse_task_id = _uuid_or_none(source_document.document_metadata.get("parse_task_id"))
    if parse_task_id is not None:
        task = session.get(ParseTask, parse_task_id)
        if task is not None:
            return task
    return session.exec(select(ParseTask).where(ParseTask.raw_content_ref == source_document.storage_uri)).first()


def _hydrate_source_document_metadata(session: Session, documents: list[SourceDocument], context: WorkspaceContextDep) -> None:
    if not documents:
        return
    nodes = session.exec(select(WikiNode).where(_workspace_filter(WikiNode, context))).all()
    nodes_by_source_id = {
        str(node.yaml_meta.get("source_document_id")): node
        for node in nodes
        if node.yaml_meta.get("source_document_id")
    }
    for document in documents:
        node = nodes_by_source_id.get(str(document.id))
        if node is None:
            continue
        metadata = dict(document.document_metadata or {})
        if "llm_extraction" not in metadata and node.yaml_meta.get("llm_extraction"):
            metadata["llm_extraction"] = node.yaml_meta.get("llm_extraction")
        if "analysis_status" not in metadata and node.yaml_meta.get("analysis_status"):
            metadata["analysis_status"] = node.yaml_meta.get("analysis_status")
        if "wiki_node_id" not in metadata:
            metadata["wiki_node_id"] = str(node.id)
        document.document_metadata = metadata


def _related_nodes_for(session: Session, node: WikiNode, context: WorkspaceContextDep, limit: int = 24) -> list[dict[str, object]]:
    edges = session.exec(select(KnowledgeEdge).where(_workspace_filter(KnowledgeEdge, context))).all()
    related: list[dict[str, object]] = []
    seen: set[UUID] = set()
    for edge in edges:
        if edge.src_node_id == node.id:
            related_id = edge.tgt_node_id
            direction = "out"
        elif edge.tgt_node_id == node.id:
            related_id = edge.src_node_id
            direction = "in"
        else:
            continue
        if related_id in seen:
            continue
        related_node = session.get(WikiNode, related_id)
        if related_node is None or not _is_workspace_record(related_node, context) or not _is_visible_wiki_node(related_node):
            continue
        seen.add(related_id)
        related.append(
            {
                "id": str(related_node.id),
                "title": related_node.title,
                "type": related_node.type,
                "relation_type": edge.relation_type,
                "direction": direction,
                "description": related_node.yaml_meta.get("description") or related_node.yaml_meta.get("summary"),
            }
        )
        if len(related) >= limit:
            break
    return related


@router.get("/domain-packs", response_model=list[DomainPackRead])
def list_domain_packs(session: SessionDep, context: WorkspaceContextDep, active_only: bool = True) -> list[DomainPack]:
    stmt = (
        select(DomainPack)
        .where(_workspace_or_system_filter(DomainPack, context))
        .order_by(col(DomainPack.slug).asc(), col(DomainPack.version).desc())
    )
    if active_only:
        stmt = stmt.where(DomainPack.is_active == True)
    return session.exec(stmt).all()


@router.post("/domain-packs", response_model=DomainPackRead)
def create_domain_pack(payload: DomainPackCreate, session: SessionDep, context: WorkspaceContextDep) -> DomainPack:
    existing = session.exec(
        select(DomainPack).where(_workspace_filter(DomainPack, context), DomainPack.slug == payload.slug, DomainPack.version == payload.version)
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="domain pack slug/version already exists")
    pack = DomainPack(
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        owner_type="user",
        version=payload.version,
        is_active=payload.is_active,
        config=payload.config,
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
    )
    session.add(pack)
    session.commit()
    session.refresh(pack)
    return pack


@router.put("/domain-packs/{domain_pack_id}", response_model=DomainPackRead)
def update_domain_pack(domain_pack_id: UUID, payload: DomainPackUpdate, session: SessionDep, context: WorkspaceContextDep) -> DomainPack:
    pack = session.get(DomainPack, domain_pack_id)
    if pack is None or not _is_workspace_record(pack, context):
        raise HTTPException(status_code=404, detail="domain pack not found")
    if pack.owner_type == "system":
        raise HTTPException(status_code=403, detail="system domain pack is read only")
    if payload.name is not None:
        pack.name = payload.name
    if payload.description is not None:
        pack.description = payload.description
    if payload.is_active is not None:
        pack.is_active = payload.is_active
    if payload.config is not None:
        pack.config = payload.config
    pack.updated_at = utcnow()
    session.add(pack)
    session.commit()
    session.refresh(pack)
    return pack


@router.delete("/domain-packs/{domain_pack_id}", response_model=OperationResponse)
def delete_domain_pack(domain_pack_id: UUID, session: SessionDep, context: WorkspaceContextDep) -> OperationResponse:
    pack = session.get(DomainPack, domain_pack_id)
    if pack is None or not _is_workspace_record(pack, context):
        raise HTTPException(status_code=404, detail="domain pack not found")
    if pack.owner_type == "system":
        raise HTTPException(status_code=403, detail="system domain pack is read only")
    bindings = session.exec(
        select(DomainPackBinding).where(_workspace_filter(DomainPackBinding, context), DomainPackBinding.domain_pack_id == domain_pack_id)
    ).all()
    for binding in bindings:
        session.delete(binding)
    session.delete(pack)
    message = "domain pack deleted"
    session.commit()
    return OperationResponse(ok=True, message=message, details={"removed_bindings": len(bindings)})


@router.get("/domains", response_model=list[DomainRead])
def list_domains(session: SessionDep, context: WorkspaceContextDep, active_only: bool = True) -> list[DomainRead]:
    stmt = select(Domain).where(_workspace_or_system_filter(Domain, context)).order_by(col(Domain.created_at).asc())
    if active_only:
        stmt = stmt.where(Domain.is_active == True)
    return [_domain_to_read(session, domain, context) for domain in session.exec(stmt).all()]


@router.post("/domains", response_model=DomainRead)
def create_domain(payload: DomainCreate, session: SessionDep, context: WorkspaceContextDep) -> DomainRead:
    existing = session.exec(select(Domain).where(_workspace_filter(Domain, context), Domain.slug == payload.slug)).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="domain slug already exists")
    domain = Domain(
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        owner_type="user",
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
    )
    session.add(domain)
    session.flush()
    _replace_domain_pack_bindings(session, domain.id, payload.domain_pack_ids, context)
    session.commit()
    session.refresh(domain)
    return _domain_to_read(session, domain, context)


@router.put("/domains/{domain_id}", response_model=DomainRead)
def update_domain(domain_id: UUID, payload: DomainUpdate, session: SessionDep, context: WorkspaceContextDep) -> DomainRead:
    domain = session.get(Domain, domain_id)
    if domain is None or not _is_workspace_record(domain, context):
        raise HTTPException(status_code=404, detail="domain not found")
    if domain.owner_type == "system":
        raise HTTPException(status_code=403, detail="system domain is read only")
    if payload.name is not None:
        domain.name = payload.name
    if payload.description is not None:
        domain.description = payload.description
    if payload.is_active is not None:
        domain.is_active = payload.is_active
    domain.updated_at = utcnow()
    session.add(domain)
    if payload.domain_pack_ids is not None:
        _replace_domain_pack_bindings(session, domain.id, payload.domain_pack_ids, context)
    session.commit()
    session.refresh(domain)
    return _domain_to_read(session, domain, context)


@router.delete("/domains/{domain_id}", response_model=OperationResponse)
def delete_domain(domain_id: UUID, session: SessionDep, context: WorkspaceContextDep) -> OperationResponse:
    domain = session.get(Domain, domain_id)
    if domain is None or not _is_workspace_record(domain, context):
        raise HTTPException(status_code=404, detail="domain not found")
    if domain.owner_type == "system":
        raise HTTPException(status_code=403, detail="system domain is read only")
    bindings = session.exec(select(DomainPackBinding).where(_workspace_filter(DomainPackBinding, context), DomainPackBinding.domain_id == domain_id)).all()
    for binding in bindings:
        session.delete(binding)
    session.flush()
    session.delete(domain)
    message = "domain deleted"
    session.commit()
    return OperationResponse(ok=True, message=message, details={"removed_bindings": len(bindings)})


@router.post("/admin/clear-knowledge", response_model=ClearKnowledgeResponse)
async def clear_knowledge(session: SessionDep, context: WorkspaceContextDep, payload: ClearKnowledgeRequest | None = None) -> ClearKnowledgeResponse:
    request = payload or ClearKnowledgeRequest()
    memory_result = await MemoryClient(session, owner_user_id=context.user_id).forget_workspace(context.workspace_id)
    if not memory_result.get("ok"):
        raise HTTPException(status_code=502, detail="could not clear external workspace memory")
    refs = _collect_source_refs(session, context) if request.delete_source_files else []
    deleted = {
        "evidence_links": _delete_records(session, EvidenceLink, context),
        "knowledge_edges": _delete_records(session, KnowledgeEdge, context),
        "source_spans": _delete_records(session, SourceSpan, context),
        "parsed_artifacts": _delete_records(session, ParsedArtifact, context),
        "parse_tasks": _delete_records(session, ParseTask, context),
        "wiki_nodes": _delete_records(session, WikiNode, context),
        "source_documents": _delete_records(session, SourceDocument, context),
        "audit_logs": _delete_records(session, AuditLog, context),
    }
    session.commit()
    if request.delete_source_files:
        deleted["source_files"] = delete_local_source_files(refs)
    return ClearKnowledgeResponse(ok=True, message="knowledge records cleared", deleted=deleted)


def _domain_to_read(session: Session, domain: Domain, context: WorkspaceContextDep) -> DomainRead:
    bindings = session.exec(
        select(DomainPackBinding)
        .where(_workspace_or_system_filter(DomainPackBinding, context), DomainPackBinding.domain_id == domain.id)
        .order_by(col(DomainPackBinding.created_at).asc())
    ).all()
    packs: list[DomainPackRead] = []
    for binding in bindings:
        pack = session.get(DomainPack, binding.domain_pack_id)
        if pack is None or not (_is_workspace_record(pack, context) or _is_system_record(pack)):
            continue
        packs.append(DomainPackRead(**pack.model_dump()))
    return DomainRead(
        id=domain.id,
        slug=domain.slug,
        name=domain.name,
        description=domain.description,
        owner_type=domain.owner_type,
        is_active=domain.is_active,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
        domain_packs=packs,
    )


def _replace_domain_pack_bindings(session: Session, domain_id: UUID, domain_pack_ids: list[UUID], context: WorkspaceContextDep) -> None:
    unique_ids = list(dict.fromkeys(domain_pack_ids))
    packs: list[DomainPack] = []
    for domain_pack_id in unique_ids:
        pack = session.get(DomainPack, domain_pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail=f"domain pack not found: {domain_pack_id}")
        if not (_is_workspace_record(pack, context) or _is_system_record(pack)):
            raise HTTPException(status_code=404, detail=f"domain pack not found: {domain_pack_id}")
        if not pack.is_active:
            raise HTTPException(status_code=400, detail=f"domain pack is inactive: {domain_pack_id}")
        packs.append(pack)

    existing = session.exec(select(DomainPackBinding).where(_workspace_filter(DomainPackBinding, context), DomainPackBinding.domain_id == domain_id)).all()
    for binding in existing:
        session.delete(binding)
    for pack in packs:
        session.add(
            DomainPackBinding(
                domain_id=domain_id,
                domain_pack_id=pack.id,
                workspace_id=context.workspace_id,
                owner_user_id=context.user_id,
            )
        )


def _collect_source_refs(session: Session, context: WorkspaceContextDep) -> list[str]:
    refs: list[str] = []
    for source in session.exec(select(SourceDocument).where(_workspace_filter(SourceDocument, context))).all():
        refs.append(source.storage_uri)
    for node in session.exec(select(WikiNode).where(_workspace_filter(WikiNode, context))).all():
        if node.raw_content_ref:
            refs.append(node.raw_content_ref)
    for task in session.exec(select(ParseTask).where(_workspace_filter(ParseTask, context))).all():
        if task.raw_content_ref:
            refs.append(task.raw_content_ref)
    return refs


def _delete_records(session: Session, model: type, context: WorkspaceContextDep) -> int:
    records = session.exec(select(model).where(_workspace_filter(model, context))).all()
    for record in records:
        session.delete(record)
    if records:
        session.flush()
    return len(records)


def _delete_wiki_node_records(session: Session, node: WikiNode, context: WorkspaceContextDep) -> dict[str, int]:
    evidence_links = session.exec(
        select(EvidenceLink).where(_workspace_filter(EvidenceLink, context), EvidenceLink.target_type == "wiki_node", EvidenceLink.target_id == str(node.id))
    ).all()
    knowledge_edges = [
        edge
        for edge in session.exec(select(KnowledgeEdge).where(_workspace_filter(KnowledgeEdge, context))).all()
        if edge.src_node_id == node.id or edge.tgt_node_id == node.id
    ]
    for record in [*evidence_links, *knowledge_edges]:
        session.delete(record)
    session.delete(node)
    session.flush()
    return {
        "evidence_links": len(evidence_links),
        "knowledge_edges": len(knowledge_edges),
        "wiki_nodes": 1,
    }


def _delete_local_source_files(refs: list[str]) -> int:
    return delete_local_source_files(refs)


@router.post("/memory/recall", response_model=RecallResponse)
async def recall(request: RecallRequest, session: SessionDep, context: WorkspaceContextDep) -> RecallResponse:
    return await build_recall_response(
        session=session,
        request=request,
        memory_client=MemoryClient(session, owner_user_id=context.user_id),
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
    )


@router.post("/memory/forget", response_model=OperationResponse)
async def forget(request: ForgetRequest, session: SessionDep, context: WorkspaceContextDep) -> OperationResponse:
    node = session.get(WikiNode, request.node_id) if request.node_id else None
    if node is not None and not _is_workspace_record(node, context):
        node = None
    if request.delete_node and node is None:
        raise HTTPException(status_code=404, detail="wiki node not found")

    doc_hash = request.doc_hash or (node.cognee_doc_hash if node is not None else None)
    if doc_hash or request.entity_urn:
        result = await MemoryClient(session, owner_user_id=context.user_id).forget(
            workspace_id=context.workspace_id,
            doc_hash=doc_hash,
            entity_urn=request.entity_urn,
        )
    else:
        result = {"ok": True, "backend": "local-only", "identifier": {"node_id": str(request.node_id)}}

    changed = 0
    deleted_local: dict[str, int] = {}
    if request.delete_node and node is not None and result.get("ok"):
        deleted_local = _delete_wiki_node_records(session, node, context)
        changed = deleted_local.get("wiki_nodes", 0)
    elif doc_hash and result.get("ok"):
        nodes = session.exec(select(WikiNode).where(_workspace_filter(WikiNode, context), WikiNode.cognee_doc_hash == doc_hash)).all()
        for node in nodes:
            node.yaml_meta = {**node.yaml_meta, "analysis_status": "deprecated"}
            node.updated_at = utcnow()
            session.add(node)
            changed += 1
    session.add(
        AuditLog(
            workspace_id=context.workspace_id,
            owner_user_id=context.user_id,
            action="forget",
            target_type="wiki_node" if request.delete_node else "memory",
            target_id=str(request.node_id) if request.delete_node and request.node_id else doc_hash or request.entity_urn or "unknown",
            payload={"reason": request.reason, "memory_result": result, "deleted_local": deleted_local},
            created_by=_actor(context),
        )
    )
    session.commit()
    message = "wiki node deleted and memory forget operation recorded" if request.delete_node else "memory forget operation recorded"
    if not result.get("ok"):
        message = "external memory deletion failed; local records were kept"
    return OperationResponse(ok=bool(result.get("ok")), message=message, details={"changed_nodes": changed, "deleted_local": deleted_local, **result})


@router.post("/memory/improve", response_model=OperationResponse)
async def improve(request: ImproveRequest, session: SessionDep, context: WorkspaceContextDep) -> OperationResponse:
    node = None
    if request.node_id:
        node = session.get(WikiNode, request.node_id)
    elif request.doc_hash:
        node = session.exec(select(WikiNode).where(_workspace_filter(WikiNode, context), WikiNode.cognee_doc_hash == request.doc_hash)).first()
    if node is None or not _is_workspace_record(node, context):
        raise HTTPException(status_code=404, detail="wiki node not found")

    new_meta = {**node.yaml_meta, request.field: request.correction, "analysis_status": "parsed"}
    node.yaml_meta = new_meta
    node.updated_at = utcnow()
    session.add(node)
    result = await MemoryClient(session, owner_user_id=context.user_id).improve(
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
        local_resource_id=str(node.id),
        doc_hash=node.cognee_doc_hash,
        field=request.field,
        correction=request.correction,
        reason=request.reason,
    )
    session.add(
        AuditLog(
            workspace_id=context.workspace_id,
            owner_user_id=context.user_id,
            action="improve",
            target_type="wiki_node",
            target_id=str(node.id),
            payload={"field": request.field, "correction": request.correction, "reason": request.reason, "memory_result": result},
            created_by=(request.updated_by or _actor(context))[:50],
        )
    )
    session.commit()
    return OperationResponse(ok=bool(result.get("ok")), message="wiki metadata corrected and memory improve operation recorded", details=result)


@router.get("/settings/llm-config", response_model=LLMConfigRead)
def get_llm_config(session: SessionDep, context: WorkspaceContextDep) -> LLMConfigRead:
    config = session.exec(
        select(LLMConfigTable).where(
            _workspace_filter(LLMConfigTable, context),
            LLMConfigTable.owner_user_id == context.user_id,
            LLMConfigTable.is_active == True,
        )
    ).first()
    if config is None:
        return _runtime_llm_config_to_read(
            LLMFactory.get_config(
                workspace_id=context.workspace_id,
                owner_user_id=context.user_id,
            )
        )
    return _llm_config_to_read(config)


@router.get("/settings/llm-configs", response_model=list[LLMConfigRead])
def list_llm_configs(session: SessionDep, context: WorkspaceContextDep) -> list[LLMConfigRead]:
    configs = session.exec(
        select(LLMConfigTable)
        .where(
            _workspace_filter(LLMConfigTable, context),
            LLMConfigTable.owner_user_id == context.user_id,
        )
        .order_by(col(LLMConfigTable.is_active).desc(), col(LLMConfigTable.updated_at).desc())
    ).all()
    return [_llm_config_to_read(config) for config in configs]


@router.post("/settings/llm-configs", response_model=LLMConfigRead)
def create_llm_config(payload: LLMConfigCreate, session: SessionDep, context: WorkspaceContextDep) -> LLMConfigRead:
    config = LLMConfigTable(
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
        profile_name=payload.profile_name.strip(),
        provider=payload.provider.strip(),
        endpoint=payload.endpoint.strip(),
        model_name=payload.model_name.strip(),
        api_key=encrypt_api_key(payload.api_key),
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        is_active=False,
        updated_by=(payload.updated_by or _actor(context))[:50],
        updated_at=utcnow(),
    )
    session.add(config)
    session.flush()
    if payload.is_active:
        _activate_llm_config(session, config.id, context)
    session.commit()
    session.refresh(config)
    LLMFactory.invalidate()
    return _llm_config_to_read(config)


@router.put("/settings/llm-configs/{config_id}", response_model=LLMConfigRead)
def update_saved_llm_config(config_id: int, payload: LLMConfigUpdate, session: SessionDep, context: WorkspaceContextDep) -> LLMConfigRead:
    config = session.get(LLMConfigTable, config_id)
    if config is None or not _is_workspace_record(config, context) or config.owner_user_id != context.user_id:
        raise HTTPException(status_code=404, detail="llm config not found")
    _apply_llm_config_payload(config, payload)
    session.add(config)
    if payload.is_active:
        _activate_llm_config(session, config.id, context)
    session.commit()
    session.refresh(config)
    LLMFactory.invalidate()
    return _llm_config_to_read(config)


@router.post("/settings/llm-configs/{config_id}/activate", response_model=LLMConfigRead)
def activate_saved_llm_config(config_id: int, session: SessionDep, context: WorkspaceContextDep) -> LLMConfigRead:
    config = session.get(LLMConfigTable, config_id)
    if config is None or not _is_workspace_record(config, context) or config.owner_user_id != context.user_id:
        raise HTTPException(status_code=404, detail="llm config not found")
    _activate_llm_config(session, config_id, context)
    session.commit()
    session.refresh(config)
    LLMFactory.invalidate()
    return _llm_config_to_read(config)


@router.delete("/settings/llm-configs/{config_id}", response_model=OperationResponse)
def delete_saved_llm_config(config_id: int, session: SessionDep, context: WorkspaceContextDep) -> OperationResponse:
    config = session.get(LLMConfigTable, config_id)
    if config is None or not _is_workspace_record(config, context) or config.owner_user_id != context.user_id:
        raise HTTPException(status_code=404, detail="llm config not found")
    if config.is_active:
        raise HTTPException(status_code=400, detail="active llm config cannot be deleted")
    session.delete(config)
    session.commit()
    return OperationResponse(ok=True, message="llm config deleted", details={"id": config_id})


@router.put("/settings/llm-config", response_model=LLMConfigRead)
def update_llm_config(payload: LLMConfigUpdate, session: SessionDep, context: WorkspaceContextDep) -> LLMConfigRead:
    config = session.get(LLMConfigTable, payload.id) if payload.id else None
    if config is not None and (
        not _is_workspace_record(config, context) or config.owner_user_id != context.user_id
    ):
        raise HTTPException(status_code=404, detail="llm config not found")
    if config is None:
        config = LLMConfigTable(
            workspace_id=context.workspace_id,
            owner_user_id=context.user_id,
            profile_name=payload.profile_name.strip(),
            provider=payload.provider.strip(),
            endpoint=payload.endpoint.strip(),
            model_name=payload.model_name.strip(),
            api_key=encrypt_api_key(payload.api_key),
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            is_active=False,
            updated_by=(payload.updated_by or _actor(context))[:50],
            updated_at=utcnow(),
        )
        session.add(config)
        session.flush()
    else:
        _apply_llm_config_payload(config, payload)
    _activate_llm_config(session, config.id, context)
    session.add(
        AuditLog(
            workspace_id=context.workspace_id,
            owner_user_id=context.user_id,
            action="update_llm_config",
            target_type="llm_config",
            target_id="active",
            payload=_audit_payload(payload.model_dump()),
            created_by=(payload.updated_by or _actor(context))[:50],
        )
    )
    session.commit()
    session.refresh(config)
    LLMFactory.invalidate()
    return _llm_config_to_read(config)


def _apply_llm_config_payload(config: LLMConfigTable, payload: LLMConfigUpdate) -> None:
    config.profile_name = payload.profile_name.strip()
    config.provider = payload.provider.strip()
    config.endpoint = payload.endpoint.strip()
    config.model_name = payload.model_name.strip()
    if payload.api_key is not None and payload.api_key.strip():
        config.api_key = encrypt_api_key(payload.api_key)
    config.temperature = payload.temperature
    config.max_tokens = payload.max_tokens
    config.updated_by = payload.updated_by
    config.updated_at = utcnow()


def _activate_llm_config(session: Session, config_id: int | None, context: WorkspaceContextDep) -> None:
    if config_id is None:
        raise HTTPException(status_code=400, detail="llm config id is required")
    configs = session.exec(
        select(LLMConfigTable).where(
            _workspace_filter(LLMConfigTable, context),
            LLMConfigTable.owner_user_id == context.user_id,
        )
    ).all()
    for config in configs:
        config.is_active = config.id == config_id
        config.updated_at = utcnow() if config.is_active else config.updated_at
        session.add(config)


def _llm_config_to_read(config: LLMConfigTable) -> LLMConfigRead:
    return LLMConfigRead(
        id=config.id,
        profile_name=config.profile_name or "未命名配置",
        provider=config.provider.strip(),
        endpoint=config.endpoint.strip(),
        model_name=config.model_name.strip(),
        has_api_key=bool(config.api_key),
        api_key_masked=_mask_api_key(decrypt_api_key(config.api_key)),
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        is_active=config.is_active,
        updated_by=config.updated_by,
        updated_at=config.updated_at,
    )


def _runtime_llm_config_to_read(config: RuntimeLLMConfig) -> LLMConfigRead:
    return LLMConfigRead(
        id=None,
        profile_name=config.profile_name,
        provider=config.provider,
        endpoint=config.effective_endpoint,
        model_name=config.model_name,
        has_api_key=bool(config.api_key),
        api_key_masked=_mask_api_key(config.api_key),
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        is_active=True,
        updated_by=None,
        updated_at=None,
    )


def _mask_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


def _audit_payload(payload: dict[str, object]) -> dict[str, object]:
    sanitized = dict(payload)
    api_key = sanitized.pop("api_key", None)
    if isinstance(api_key, str) and api_key.strip():
        sanitized["has_api_key"] = True
        sanitized["api_key_masked"] = _mask_api_key(api_key.strip())
    elif api_key is not None:
        sanitized["has_api_key"] = False
    return sanitized


@router.api_route("/settings/test-llm", methods=["GET", "POST"], response_model=LLMTestResponse)
async def test_llm_config(context: WorkspaceContextDep) -> LLMTestResponse:
    config = LLMFactory.get_config(
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
    )
    started = perf_counter()
    try:
        text = await LLMFactory.generate(
            "请只回答 OK",
            workspace_id=context.workspace_id,
            owner_user_id=context.user_id,
        )
        latency_ms = int((perf_counter() - started) * 1000)
        ok = bool(text.strip())
        return LLMTestResponse(
            ok=ok,
            provider=config.provider,
            endpoint=config.effective_endpoint,
            model_name=config.model_name,
            latency_ms=latency_ms,
            message=text.strip()[:200] or "empty model response",
        )
    except Exception as exc:
        latency_ms = int((perf_counter() - started) * 1000)
        return LLMTestResponse(
            ok=False,
            provider=config.provider,
            endpoint=config.effective_endpoint,
            model_name=config.model_name,
            latency_ms=latency_ms,
            message=str(exc),
        )


@router.get("/settings/web-search-config", response_model=WebSearchConfigRead)
def get_web_search_config(session: SessionDep, context: WorkspaceContextDep) -> WebSearchConfigRead:
    config = session.exec(
        select(WebSearchConfigTable).where(_workspace_filter(WebSearchConfigTable, context), WebSearchConfigTable.is_active == True)
    ).first()
    if config is None:
        config = session.exec(
            select(WebSearchConfigTable).where(WebSearchConfigTable.workspace_id.is_(None), WebSearchConfigTable.is_active == True)
        ).first()
    if config is None:
        raise HTTPException(status_code=404, detail="active web search config not found")
    return _web_search_config_to_read(config)


@router.get("/settings/web-search-configs", response_model=list[WebSearchConfigRead])
def list_web_search_configs(session: SessionDep, context: WorkspaceContextDep) -> list[WebSearchConfigRead]:
    configs = session.exec(
        select(WebSearchConfigTable)
        .where(_workspace_filter(WebSearchConfigTable, context))
        .order_by(col(WebSearchConfigTable.is_active).desc(), col(WebSearchConfigTable.updated_at).desc())
    ).all()
    return [_web_search_config_to_read(config) for config in configs]


@router.post("/settings/web-search-configs", response_model=WebSearchConfigRead)
def create_web_search_config(payload: WebSearchConfigCreate, session: SessionDep, context: WorkspaceContextDep) -> WebSearchConfigRead:
    config = WebSearchConfigTable(
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
        profile_name=payload.profile_name.strip(),
        provider=payload.provider.strip(),
        endpoint=payload.endpoint.strip(),
        api_key=encrypt_api_key(payload.api_key),
        command=payload.command.strip(),
        args=[str(arg).strip() for arg in payload.args if str(arg).strip()],
        tool_name=payload.tool_name.strip(),
        timeout_seconds=payload.timeout_seconds,
        max_results=payload.max_results,
        is_active=False,
        updated_by=(payload.updated_by or _actor(context))[:50],
        updated_at=utcnow(),
    )
    session.add(config)
    session.flush()
    if payload.is_active:
        _activate_web_search_config(session, config.id, context)
    session.commit()
    session.refresh(config)
    WebSearchClient.invalidate()
    return _web_search_config_to_read(config)


@router.put("/settings/web-search-configs/{config_id}", response_model=WebSearchConfigRead)
def update_saved_web_search_config(config_id: int, payload: WebSearchConfigUpdate, session: SessionDep, context: WorkspaceContextDep) -> WebSearchConfigRead:
    config = session.get(WebSearchConfigTable, config_id)
    if config is None or not _is_workspace_record(config, context):
        raise HTTPException(status_code=404, detail="web search config not found")
    _apply_web_search_config_payload(config, payload)
    session.add(config)
    if payload.is_active:
        _activate_web_search_config(session, config.id, context)
    session.commit()
    session.refresh(config)
    WebSearchClient.invalidate()
    return _web_search_config_to_read(config)


@router.post("/settings/web-search-configs/{config_id}/activate", response_model=WebSearchConfigRead)
def activate_saved_web_search_config(config_id: int, session: SessionDep, context: WorkspaceContextDep) -> WebSearchConfigRead:
    config = session.get(WebSearchConfigTable, config_id)
    if config is None or not _is_workspace_record(config, context):
        raise HTTPException(status_code=404, detail="web search config not found")
    _activate_web_search_config(session, config_id, context)
    session.commit()
    session.refresh(config)
    WebSearchClient.invalidate()
    return _web_search_config_to_read(config)


@router.delete("/settings/web-search-configs/{config_id}", response_model=OperationResponse)
def delete_saved_web_search_config(config_id: int, session: SessionDep, context: WorkspaceContextDep) -> OperationResponse:
    config = session.get(WebSearchConfigTable, config_id)
    if config is None or not _is_workspace_record(config, context):
        raise HTTPException(status_code=404, detail="web search config not found")
    if config.is_active:
        raise HTTPException(status_code=400, detail="active web search config cannot be deleted")
    session.delete(config)
    session.commit()
    WebSearchClient.invalidate()
    return OperationResponse(ok=True, message="web search config deleted", details={"id": config_id})


def _apply_web_search_config_payload(config: WebSearchConfigTable, payload: WebSearchConfigUpdate) -> None:
    config.profile_name = payload.profile_name.strip()
    config.provider = payload.provider.strip()
    config.endpoint = payload.endpoint.strip()
    if payload.api_key is not None and payload.api_key.strip():
        config.api_key = encrypt_api_key(payload.api_key)
    config.command = payload.command.strip()
    config.args = [str(arg).strip() for arg in payload.args if str(arg).strip()]
    config.tool_name = payload.tool_name.strip()
    config.timeout_seconds = payload.timeout_seconds
    config.max_results = payload.max_results
    config.updated_by = payload.updated_by
    config.updated_at = utcnow()


def _activate_web_search_config(session: Session, config_id: int | None, context: WorkspaceContextDep) -> None:
    if config_id is None:
        raise HTTPException(status_code=400, detail="web search config id is required")
    configs = session.exec(select(WebSearchConfigTable).where(_workspace_filter(WebSearchConfigTable, context))).all()
    for config in configs:
        config.is_active = config.id == config_id
        config.updated_at = utcnow() if config.is_active else config.updated_at
        session.add(config)


def _web_search_config_to_read(config: WebSearchConfigTable) -> WebSearchConfigRead:
    return WebSearchConfigRead(
        id=config.id,
        profile_name=config.profile_name or "MiniMax Web Search",
        provider=config.provider.strip(),
        endpoint=config.endpoint.strip(),
        has_api_key=bool(config.api_key),
        api_key_masked=_mask_api_key(decrypt_api_key(config.api_key)),
        command=config.command.strip(),
        args=[str(arg) for arg in (config.args or [])],
        tool_name=config.tool_name.strip(),
        timeout_seconds=config.timeout_seconds,
        max_results=config.max_results,
        is_active=config.is_active,
        updated_by=config.updated_by,
        updated_at=config.updated_at,
    )


@router.post("/settings/test-web-search", response_model=WebSearchTestResponse)
async def test_web_search_config(context: WorkspaceContextDep) -> WebSearchTestResponse:
    config = WebSearchClient.get_config(workspace_id=context.workspace_id)
    started = perf_counter()
    try:
        result = await WebSearchClient(workspace_id=context.workspace_id).search("MiniMax web search", top_k=1)
        return WebSearchTestResponse(
            ok=bool(result.results),
            provider=config.provider,
            endpoint=config.endpoint,
            latency_ms=result.latency_ms,
            message=f"returned {len(result.results)} result(s)",
            results=result.results,
        )
    except Exception as exc:
        return WebSearchTestResponse(
            ok=False,
            provider=config.provider,
            endpoint=config.endpoint,
            latency_ms=int((perf_counter() - started) * 1000),
            message=str(exc),
            results=[],
        )


@router.post("/web-search/search", response_model=WebSearchResponse)
async def web_search(request: WebSearchRequest, context: WorkspaceContextDep) -> WebSearchResponse:
    return await WebSearchClient(workspace_id=context.workspace_id).search(request.query, top_k=request.top_k)


@router.get("/graph/nodes", response_model=GraphResponse)
def graph_nodes(session: SessionDep, context: WorkspaceContextDep, type: str | None = None, ticker: str | None = None, limit: int = 200) -> GraphResponse:
    stmt = select(WikiNode).where(_workspace_filter(WikiNode, context)).order_by(col(WikiNode.updated_at).desc()).limit(min(limit, 500))
    if type:
        stmt = stmt.where(WikiNode.type == type)
    nodes = [node for node in session.exec(stmt).all() if _is_visible_wiki_node(node)]
    if ticker:
        nodes = [node for node in nodes if _ticker_matches(node.yaml_meta.get("ticker"), ticker)]
    node_ids = {node.id for node in nodes}
    edges = session.exec(select(KnowledgeEdge).where(_workspace_filter(KnowledgeEdge, context))).all()
    graph_edges: list[GraphEdge] = []
    edge_keys: set[tuple[str, str, str]] = set()
    for edge in edges:
        if edge.src_node_id not in node_ids or edge.tgt_node_id not in node_ids:
            continue
        graph_edge = GraphEdge(
            id=f"{edge.src_node_id}:{edge.tgt_node_id}:{edge.relation_type}",
            source=str(edge.src_node_id),
            target=str(edge.tgt_node_id),
            relation_type=edge.relation_type,
            weight=edge.weight,
            metadata=edge.edge_metadata,
        )
        graph_edges.append(graph_edge)
        edge_keys.add((graph_edge.source, graph_edge.target, graph_edge.relation_type))
    _append_inferred_graph_edges(nodes, graph_edges, edge_keys)
    return GraphResponse(
        nodes=[
            GraphNode(
                id=str(node.id),
                label=node.title,
                type=node.type,
                ticker=_str_or_none(node.yaml_meta.get("ticker")),
                company_name=_str_or_none(node.yaml_meta.get("company_name")),
                company_short_name=_str_or_none(node.yaml_meta.get("company_short_name")),
                report_year=_safe_int(node.yaml_meta.get("report_year")),
                folder_path=_str_or_none(node.yaml_meta.get("folder_path")),
                status=_str_or_none(node.yaml_meta.get("analysis_status")),
                updated_at=node.updated_at,
            )
            for node in nodes
        ],
        edges=graph_edges,
    )


def _append_inferred_graph_edges(
    nodes: list[WikiNode],
    graph_edges: list[GraphEdge],
    edge_keys: set[tuple[str, str, str]],
) -> None:
    _connect_adjacent_groups(
        nodes,
        key_func=_company_key,
        relation_type="SAME_COMPANY_REPORT",
        weight=0.72,
        graph_edges=graph_edges,
        edge_keys=edge_keys,
    )
    _connect_adjacent_groups(
        nodes,
        key_func=lambda node: str(node.yaml_meta.get("folder_path") or ""),
        relation_type="SAME_FOLDER",
        weight=0.45,
        graph_edges=graph_edges,
        edge_keys=edge_keys,
    )
    _connect_adjacent_groups(
        nodes,
        key_func=lambda node: _first_tag(node),
        relation_type="SHARED_TAG",
        weight=0.36,
        graph_edges=graph_edges,
        edge_keys=edge_keys,
    )


def _connect_adjacent_groups(
    nodes: list[WikiNode],
    key_func: Callable[[WikiNode], str],
    relation_type: str,
    weight: float,
    graph_edges: list[GraphEdge],
    edge_keys: set[tuple[str, str, str]],
) -> None:
    groups: dict[str, list[WikiNode]] = {}
    for node in nodes:
        key = key_func(node)
        if key:
            groups.setdefault(str(key), []).append(node)
    for key, group_nodes in groups.items():
        if len(group_nodes) < 2:
            continue
        sorted_nodes = sorted(group_nodes, key=lambda item: (item.yaml_meta.get("report_year") or 0, item.title))
        for previous, current in zip(sorted_nodes, sorted_nodes[1:]):
            relation = relation_type
            previous_year = _safe_int(previous.yaml_meta.get("report_year"))
            current_year = _safe_int(current.yaml_meta.get("report_year"))
            if relation_type == "SAME_COMPANY_REPORT" and previous_year and current_year and abs(current_year - previous_year) == 1:
                relation = "REPORT_YEAR_SEQUENCE"
            _add_graph_edge(graph_edges, edge_keys, previous, current, relation, weight, {"inferred": True, "group": key})


def _add_graph_edge(
    graph_edges: list[GraphEdge],
    edge_keys: set[tuple[str, str, str]],
    source: WikiNode,
    target: WikiNode,
    relation_type: str,
    weight: float,
    metadata: dict[str, object],
) -> None:
    if source.id == target.id:
        return
    source_id = str(source.id)
    target_id = str(target.id)
    key = (source_id, target_id, relation_type)
    reverse_key = (target_id, source_id, relation_type)
    pair_exists = any(
        {edge.source, edge.target} == {source_id, target_id}
        for edge in graph_edges
    )
    if pair_exists or key in edge_keys or reverse_key in edge_keys:
        return
    graph_edges.append(
        GraphEdge(
            id=f"{source.id}:{target.id}:{relation_type}",
            source=source_id,
            target=target_id,
            relation_type=relation_type,
            weight=weight,
            metadata=metadata,
        )
    )
    edge_keys.add(key)


def _company_key(node: WikiNode) -> str:
    metadata = node.yaml_meta or {}
    ticker = _normalize_ticker(metadata.get("ticker"))
    return ticker or str(metadata.get("company_short_name") or metadata.get("company_name") or "")


def _normalize_ticker(value: object) -> str:
    text = str(value or "").strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix) and text[len(prefix) :].isdigit():
            return text[len(prefix) :]
    if "." in text:
        code, market = text.split(".", 1)
        if market in {"SH", "SZ", "BJ"} and code.isdigit():
            return code
    return text


def _ticker_matches(value: object, expected: object) -> bool:
    left = _normalize_ticker(value)
    right = _normalize_ticker(expected)
    return bool(left and right and left == right)


def _first_tag(node: WikiNode) -> str:
    tags = node.yaml_meta.get("tags")
    if isinstance(tags, list) and tags:
        return str(tags[0])
    return ""


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@router.post("/scoring/evaluate", response_model=ScoringResponse)
def scoring(request: ScoringRequest, session: SessionDep, context: WorkspaceContextDep) -> ScoringResponse:
    node = session.get(WikiNode, request.node_id)
    if node is None or not _is_workspace_record(node, context):
        raise HTTPException(status_code=404, detail="wiki node not found")
    return evaluate_value_score(node=node, weights=request.weights)


@router.get("/chat/conversations", response_model=list[ChatConversationRead])
def list_chat_conversations(session: SessionDep, context: WorkspaceContextDep, limit: int = 50) -> list[ChatConversationRead]:
    conversations = session.exec(
        select(ChatConversation)
        .where(_workspace_filter(ChatConversation, context), ChatConversation.owner_user_id == context.user_id)
        .order_by(col(ChatConversation.updated_at).desc())
        .limit(min(limit, 100))
    ).all()
    return [_chat_conversation_to_read(conversation, []) for conversation in conversations]


@router.get("/chat/conversations/{conversation_id}", response_model=ChatConversationRead)
def get_chat_conversation(conversation_id: UUID, session: SessionDep, context: WorkspaceContextDep) -> ChatConversationRead:
    conversation = session.get(ChatConversation, conversation_id)
    _ensure_chat_conversation(conversation, context)
    messages = session.exec(
        select(ChatMessage)
        .where(_workspace_filter(ChatMessage, context), ChatMessage.conversation_id == conversation_id)
        .order_by(col(ChatMessage.created_at).asc())
    ).all()
    return _chat_conversation_to_read(conversation, messages)


@router.put("/chat/conversations/{conversation_id}", response_model=ChatConversationRead)
def update_chat_conversation(
    conversation_id: UUID,
    payload: ChatConversationUpdate,
    session: SessionDep,
    context: WorkspaceContextDep,
) -> ChatConversationRead:
    conversation = session.get(ChatConversation, conversation_id)
    _ensure_chat_conversation(conversation, context)
    conversation.title = payload.title.strip()
    conversation.updated_at = utcnow()
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return _chat_conversation_to_read(conversation, [])


@router.delete("/chat/conversations/{conversation_id}", response_model=OperationResponse)
def delete_chat_conversation(conversation_id: UUID, session: SessionDep, context: WorkspaceContextDep) -> OperationResponse:
    conversation = session.get(ChatConversation, conversation_id)
    _ensure_chat_conversation(conversation, context)
    messages = session.exec(
        select(ChatMessage).where(_workspace_filter(ChatMessage, context), ChatMessage.conversation_id == conversation_id)
    ).all()
    for message in messages:
        session.delete(message)
    session.delete(conversation)
    session.commit()
    return OperationResponse(ok=True, message="conversation deleted", details={"messages": len(messages)})


@router.post("/agent/dialog", response_model=RecallResponse)
async def agent_dialog(request: DialogRequest, session: SessionDep, context: WorkspaceContextDep) -> RecallResponse:
    conversation = _get_or_create_conversation(session, request, context)
    recent_messages = _recent_conversation_messages(session, conversation, context)
    session.add(
        ChatMessage(
            conversation_id=conversation.id,
            workspace_id=context.workspace_id,
            owner_user_id=context.user_id,
            role="user",
            content=request.query,
        )
    )
    session.commit()
    recall_request = RecallRequest(query=request.query, top_k=request.top_k, use_web_search=request.use_web_search)
    response = await build_recall_response(
        session=session,
        request=recall_request,
        memory_client=MemoryClient(session, owner_user_id=context.user_id),
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
        conversation_history=[{"role": message.role, "content": message.content} for message in recent_messages],
    )
    session.add(
        ChatMessage(
            conversation_id=conversation.id,
            workspace_id=context.workspace_id,
            owner_user_id=context.user_id,
            role="assistant",
            content=response.answer,
            citations=[citation.model_dump(mode="json") for citation in response.citations],
            confidence=response.confidence,
            memory_backend=response.memory_backend,
        )
    )
    conversation.updated_at = utcnow()
    session.add(conversation)
    session.commit()
    return response.model_copy(update={"conversation_id": conversation.id})


def _get_or_create_conversation(session: Session, request: DialogRequest, context: WorkspaceContextDep) -> ChatConversation:
    if request.conversation_id is not None:
        conversation = session.get(ChatConversation, request.conversation_id)
        _ensure_chat_conversation(conversation, context)
        return conversation
    title = re.sub(r"\s+", " ", request.query).strip()[:48] or "新对话"
    conversation = ChatConversation(
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
        title=title,
    )
    session.add(conversation)
    session.flush()
    return conversation


def _ensure_chat_conversation(conversation: ChatConversation | None, context: WorkspaceContextDep) -> None:
    if conversation is None or conversation.workspace_id != context.workspace_id or conversation.owner_user_id != context.user_id:
        raise HTTPException(status_code=404, detail="conversation not found")


def _recent_conversation_messages(
    session: Session,
    conversation: ChatConversation,
    context: WorkspaceContextDep,
    limit: int = 12,
) -> list[ChatMessage]:
    messages = session.exec(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation.id,
            ChatMessage.workspace_id == context.workspace_id,
            ChatMessage.owner_user_id == context.user_id,
        )
        .order_by(col(ChatMessage.created_at).desc())
        .limit(limit)
    ).all()
    return list(reversed(messages))


def _chat_conversation_to_read(conversation: ChatConversation, messages: list[ChatMessage]) -> ChatConversationRead:
    return ChatConversationRead(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            ChatMessageRead(
                id=message.id,
                conversation_id=message.conversation_id,
                role=message.role,
                content=message.content,
                citations=message.citations,
                confidence=message.confidence,
                memory_backend=message.memory_backend,
                created_at=message.created_at,
            )
            for message in messages
        ],
    )


@router.get("/debug/settings")
def debug_settings() -> dict[str, str | bool]:
    settings = get_settings()
    return {
        "database_configured": bool(settings.database_url),
        "storage_configured": bool(settings.storage_dir),
        "cognee_enabled": settings.cognee_enabled,
    }
