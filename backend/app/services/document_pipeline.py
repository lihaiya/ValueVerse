import re
from hashlib import sha256
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.session import get_engine
from app.models import AuditLog, EvidenceLink, KnowledgeEdge, ParsedArtifact, ParseStatus, ParseTask, SourceDocument, SourceSpan, WikiNode, utcnow
from app.services.knowledge_delete import delete_source_document_records
from app.services.llm_extractor import extract_wiki_with_llm
from app.services.memory import MemoryClient
from app.services.metadata import build_markdown, compute_doc_hash, infer_metadata
from app.services.parser import parse_document
from app.services.storage import get_object_storage


class PipelineCancelled(Exception):
    def __init__(self, delete_after: bool = False) -> None:
        self.delete_after = delete_after
        super().__init__("parse task cancelled")


async def stage_upload(
    file: UploadFile,
    task: ParseTask,
    session: Session,
    selected_domain_id: UUID | None = None,
    selected_domain_pack_ids: list[UUID] | None = None,
    folder_path: str | None = None,
    workspace_id: UUID | None = None,
    owner_user_id: UUID | None = None,
) -> SourceDocument:
    settings = get_settings()
    storage = get_object_storage()
    content = await _read_upload_bytes(file, settings.max_upload_bytes)
    stored = storage.save(data=content, filename=file.filename or "upload.txt", category="raw")
    raw_hash = sha256(content).hexdigest()
    normalized_folder_path = _normalize_folder_path(folder_path)

    task.status = ParseStatus.parsing
    task.workspace_id = workspace_id or task.workspace_id
    task.owner_user_id = owner_user_id or task.owner_user_id
    task.progress = 10
    task.message = "Document uploaded; parser queued"
    task.raw_content_ref = stored.uri
    task.updated_at = utcnow()
    session.add(task)

    source_document = SourceDocument(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        filename=file.filename or "upload",
        mime_type=file.content_type,
        storage_backend=stored.backend,
        storage_uri=stored.uri,
        sha256=raw_hash,
        size_bytes=stored.size_bytes,
        status="uploaded",
        document_metadata={
            "storage_dir": str(settings.storage_dir),
            "parse_task_id": str(task.id),
            "selected_domain_id": str(selected_domain_id) if selected_domain_id is not None else None,
            "selected_domain_pack_ids": [str(pack_id) for pack_id in selected_domain_pack_ids or []],
            "folder_path": normalized_folder_path,
        },
    )
    session.add(source_document)
    session.commit()
    session.refresh(source_document)
    session.refresh(task)
    return source_document


async def _read_upload_bytes(file: UploadFile, max_bytes: int) -> bytes:
    chunks = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        if len(chunks) + len(chunk) > max_bytes:
            raise HTTPException(status_code=413, detail=f"uploaded file exceeds {max_bytes} bytes")
        chunks.extend(chunk)
    return bytes(chunks)


async def process_staged_upload(
    task_id: UUID,
    source_document_id: UUID,
    selected_domain_id: UUID | None = None,
    selected_domain_pack_ids: list[UUID] | None = None,
    folder_path: str | None = None,
    workspace_id: UUID | None = None,
    owner_user_id: UUID | None = None,
) -> None:
    with Session(get_engine()) as session:
        task = session.get(ParseTask, task_id)
        source_document = session.get(SourceDocument, source_document_id)
        if task is None or source_document is None:
            return
        try:
            await _process_staged_upload(
                task=task,
                source_document=source_document,
                session=session,
                memory_client=MemoryClient(session),
                selected_domain_id=selected_domain_id,
                selected_domain_pack_ids=selected_domain_pack_ids or [],
                folder_path=folder_path,
                workspace_id=workspace_id or source_document.workspace_id,
                owner_user_id=owner_user_id or source_document.owner_user_id,
            )
        except PipelineCancelled as exc:
            source_document = session.get(SourceDocument, source_document_id)
            task = session.get(ParseTask, task_id)
            if source_document is None:
                return
            if exc.delete_after:
                delete_source_file = bool((source_document.document_metadata or {}).get("delete_source_file"))
                memory_result = await MemoryClient(session).forget(
                    workspace_id=workspace_id or source_document.workspace_id,
                    local_resource_type="source_document",
                    local_resource_id=str(source_document.id),
                )
                if not memory_result.get("ok"):
                    source_document.status = "delete_failed"
                    source_document.document_metadata = {
                        **source_document.document_metadata,
                        "memory_delete": memory_result,
                    }
                    session.add(source_document)
                    session.commit()
                    return
                deleted = delete_source_document_records(session, source_document, delete_source_file=delete_source_file)
                session.add(
                    AuditLog(
                        workspace_id=workspace_id or source_document.workspace_id,
                        owner_user_id=owner_user_id or source_document.owner_user_id,
                        action="delete_source_document",
                        target_type="source_document",
                        target_id=str(source_document_id),
                        payload={"reason": "cancelled_during_processing", "deleted": deleted, "memory_forget": memory_result},
                        created_by=str(owner_user_id or "user"),
                    )
                )
                session.commit()
                return
            if task is not None:
                task.status = ParseStatus.cancelled
                task.progress = 100
                task.message = "Document processing cancelled"
                task.updated_at = utcnow()
                session.add(task)
            source_document.status = "cancelled"
            source_document.document_metadata = {**source_document.document_metadata, "analysis_status": "cancelled"}
            source_document.updated_at = utcnow()
            session.add(source_document)
            session.commit()
        except Exception as exc:  # pragma: no cover - surfaced through API
            task.status = ParseStatus.failed
            task.progress = 100
            task.message = str(exc)
            task.updated_at = utcnow()
            source_document.status = "failed"
            source_document.document_metadata = {**source_document.document_metadata, "error": str(exc)}
            source_document.updated_at = utcnow()
            session.add(task)
            session.add(source_document)
            session.commit()


async def process_upload(file: UploadFile, task: ParseTask, session: Session, memory_client: MemoryClient) -> WikiNode:
    source_document = await stage_upload(file=file, task=task, session=session, workspace_id=task.workspace_id, owner_user_id=task.owner_user_id)
    return await _process_staged_upload(
        task=task,
        source_document=source_document,
        session=session,
        memory_client=memory_client,
    )


async def _process_staged_upload(
    task: ParseTask,
    source_document: SourceDocument,
    session: Session,
    memory_client: MemoryClient,
    selected_domain_id: UUID | None = None,
    selected_domain_pack_ids: list[UUID] | None = None,
    folder_path: str | None = None,
    workspace_id: UUID | None = None,
    owner_user_id: UUID | None = None,
) -> WikiNode:
    settings = get_settings()
    storage = get_object_storage()
    raw_path = storage.resolve(source_document.storage_uri)
    normalized_folder_path = _normalize_folder_path(folder_path or source_document.document_metadata.get("folder_path"))
    workspace_id = workspace_id or source_document.workspace_id
    owner_user_id = owner_user_id or source_document.owner_user_id

    task.status = ParseStatus.parsing
    task.workspace_id = workspace_id or task.workspace_id
    task.owner_user_id = owner_user_id or task.owner_user_id
    task.progress = 20
    task.message = "Parsing document text"
    task.updated_at = utcnow()
    source_document.status = "parsing"
    source_document.workspace_id = workspace_id or source_document.workspace_id
    source_document.owner_user_id = owner_user_id or source_document.owner_user_id
    source_document.updated_at = utcnow()
    session.add(task)
    session.add(source_document)
    session.commit()
    _raise_if_cancelled(session, source_document.id, task.id)

    parsed = parse_document(raw_path)
    _raise_if_cancelled(session, source_document.id, task.id)
    metadata = infer_metadata(parsed.text, raw_path, parsed.parser_name, parsed.warnings)
    metadata["source_document_id"] = str(source_document.id)
    if workspace_id is not None:
        metadata["workspace_id"] = str(workspace_id)
    if owner_user_id is not None:
        metadata["owner_user_id"] = str(owner_user_id)
    metadata["parser_quality"] = parsed.quality
    if selected_domain_id is not None:
        metadata["selected_domain_id"] = str(selected_domain_id)
    if selected_domain_pack_ids:
        metadata["selected_domain_pack_ids"] = [str(pack_id) for pack_id in selected_domain_pack_ids]
    metadata["folder_path"] = normalized_folder_path

    source_document.status = "extracting" if settings.llm_extraction_enabled else "parsing"
    source_document.document_metadata = {
        **source_document.document_metadata,
        "parser_name": parsed.parser_name,
        "quality": parsed.quality,
        "selected_domain_id": str(selected_domain_id) if selected_domain_id is not None else None,
        "selected_domain_pack_ids": [str(pack_id) for pack_id in selected_domain_pack_ids or []],
        "folder_path": normalized_folder_path,
    }
    source_document.updated_at = utcnow()
    task.progress = 45
    task.message = "Extracting Wiki structure with LLM" if settings.llm_extraction_enabled else "Building Wiki without LLM"
    task.updated_at = utcnow()
    session.add(source_document)
    session.add(task)

    parsed_artifact = ParsedArtifact(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        source_document_id=source_document.id,
        parser_name=parsed.parser_name,
        artifact_type="text",
        content_text=parsed.text,
        quality=parsed.quality,
    )
    session.add(parsed_artifact)
    session.commit()
    session.refresh(parsed_artifact)
    source_spans = _persist_source_spans(session, source_document, parsed_artifact, parsed.spans, workspace_id, owner_user_id)
    _raise_if_cancelled(session, source_document.id, task.id)

    if settings.llm_extraction_enabled:
        try:
            extraction = await extract_wiki_with_llm(parsed.text, metadata, raw_path, workspace_id=workspace_id)
            _raise_if_cancelled(session, source_document.id, task.id)
            metadata = extraction.metadata
            markdown = extraction.markdown
            session.add(
                AuditLog(
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    action="llm_extract",
                    target_type="parse_task",
                    target_id=str(task.id),
                    payload={
                        "status": "completed",
                        "model": metadata.get("llm_extraction", {}).get("model"),
                        "endpoint": metadata.get("llm_extraction", {}).get("endpoint"),
                        "response_chars": len(extraction.raw_response),
                        "source_document_id": str(source_document.id),
                    },
                    created_by="system",
                )
            )
        except Exception as exc:
            metadata["llm_extraction"] = {
                "status": "failed",
                "error": str(exc),
            }
            metadata["analysis_status"] = "parsed_with_llm_fallback"
            markdown = build_markdown(parsed.text, metadata)
            session.add(
                AuditLog(
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    action="llm_extract",
                    target_type="parse_task",
                    target_id=str(task.id),
                    payload={"status": "failed", "error": str(exc)},
                    created_by="system",
                )
            )
    else:
        metadata["llm_extraction"] = {"status": "disabled"}
        metadata["analysis_status"] = "parsed_with_llm_disabled"
        markdown = build_markdown(parsed.text, metadata)

    metadata["source_document_id"] = str(source_document.id)
    metadata["raw_content_ref"] = source_document.storage_uri
    metadata["parser_quality"] = parsed.quality
    metadata["executives"] = _merge_named_items(metadata.get("executives"), _extract_executives(parsed.text))
    metadata["domain_pack_candidates"] = _infer_domain_pack_candidates(metadata, parsed.text)
    doc_hash = compute_doc_hash(markdown, raw_path)

    if metadata.get("financial_reconciliation", {}).get("status") == "warning":
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                action="financial_reconciliation_warning",
                target_type="parse_task",
                target_id=str(task.id),
                payload=metadata["financial_reconciliation"],
                created_by="system",
            )
        )

    task.progress = 75
    task.message = "Writing Wiki node and evidence links"
    task.updated_at = utcnow()
    source_document.document_metadata = {
        **source_document.document_metadata,
        "pending_cognee_doc_hash": doc_hash,
    }
    source_document.updated_at = utcnow()
    session.add(source_document)
    session.add(task)
    session.commit()
    _raise_if_cancelled(session, source_document.id, task.id)

    memory_result = await memory_client.remember(
        content=markdown,
        metadata=metadata,
        doc_hash=doc_hash,
        local_resource_type="source_document",
        local_resource_id=str(source_document.id),
    )
    metadata["memory_sync"] = {
        "status": memory_result.get("sync_status", "failed"),
        "provider": memory_result.get("backend"),
        "error": memory_result.get("error"),
    }
    _raise_if_cancelled(session, source_document.id, task.id)
    node = WikiNode(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        title=metadata["title"],
        type=metadata["type"],
        yaml_meta=metadata,
        content_md=markdown,
        raw_content_ref=source_document.storage_uri,
        cognee_doc_hash=memory_result.get("doc_hash", doc_hash),
    )
    session.add(node)
    session.flush()
    _persist_wiki_evidence_links(session, node, source_spans, workspace_id, owner_user_id)
    related_counts = _upsert_related_nodes_and_edges(session, node, metadata, source_document, markdown)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            action="remember",
            target_type="wiki_node",
            target_id=str(node.id),
            payload={
                "doc_hash": node.cognee_doc_hash,
                "memory_backend": memory_result.get("backend"),
                "source_document_id": str(source_document.id),
                "related_counts": related_counts,
            },
            created_by="system",
        )
    )
    source_document.status = "parsed"
    if metadata.get("llm_extraction", {}).get("status") == "failed" or not memory_result.get("ok"):
        source_document.status = "parsed_with_warnings"
    source_document.updated_at = utcnow()
    source_document.document_metadata = {
        **source_document.document_metadata,
        "analysis_status": metadata.get("analysis_status"),
        "llm_extraction": metadata.get("llm_extraction"),
        "memory_sync": metadata.get("memory_sync"),
        "wiki_node_id": str(node.id),
        "folder_path": normalized_folder_path,
        "pending_cognee_doc_hash": None,
    }
    task.status = ParseStatus.completed
    task.progress = 100
    task.message = _completion_message(metadata)
    task.wiki_node_id = node.id
    task.raw_content_ref = source_document.storage_uri
    task.updated_at = utcnow()
    session.add(source_document)
    session.add(task)
    session.commit()
    session.refresh(node)
    return node


def _raise_if_cancelled(session: Session, source_document_id: UUID, task_id: UUID) -> None:
    session.expire_all()
    source_document = session.get(SourceDocument, source_document_id)
    task = session.get(ParseTask, task_id)
    if source_document is None:
        raise PipelineCancelled(delete_after=False)
    if source_document.status in {"cancel_requested", "deleting"}:
        raise PipelineCancelled(delete_after=source_document.status == "deleting")
    if task is not None and task.status == ParseStatus.cancelled:
        raise PipelineCancelled(delete_after=source_document.status == "deleting")


def _upsert_related_nodes_and_edges(
    session: Session,
    report_node: WikiNode,
    metadata: dict[str, object],
    source_document: SourceDocument,
    markdown: str = "",
) -> dict[str, int]:
    created_or_updated = 0
    edges = 0
    company = _upsert_company_node(session, metadata, source_document)
    if company is not None:
        created_or_updated += 1
        edges += _ensure_edge(session, report_node, company, "ABOUT_COMPANY", 0.95, {"source": "metadata"})

    for item in _extract_related_concepts(metadata, markdown):
        concept = _upsert_concept_node(session, item, metadata, source_document)
        if concept is None:
            continue
        created_or_updated += 1
        edges += _ensure_edge(
            session,
            report_node,
            concept,
            item["relation_type"],
            float(item.get("weight") or 0.62),
            {"source": "metadata", "field": item.get("field")},
        )
        if company is not None:
            edges += _ensure_edge(
                session,
                company,
                concept,
                "HAS_CONCEPT",
                0.45,
                {"source": "metadata", "field": item.get("field")},
            )
    return {"nodes": created_or_updated, "edges": edges}


def _upsert_company_node(session: Session, metadata: dict[str, object], source_document: SourceDocument) -> WikiNode | None:
    title = str(metadata.get("company_short_name") or metadata.get("company_name") or "").strip()
    if not title:
        return None
    ticker = str(metadata.get("ticker") or "").strip()
    nodes = session.exec(
        select(WikiNode).where(WikiNode.workspace_id == source_document.workspace_id, WikiNode.type == "company-profile")
    ).all()
    node = next(
        (
            item
            for item in nodes
            if (ticker and str(item.yaml_meta.get("ticker") or "") == ticker) or item.title == title
        ),
        None,
    )
    yaml_meta = _merge_source_ids(
        {
            "title": title,
            "type": "company-profile",
            "ticker": ticker or None,
            "company_name": metadata.get("company_name") or title,
            "company_short_name": metadata.get("company_short_name") or title,
            "tags": ["公司"],
            "analysis_status": "parsed",
        },
        source_document,
        existing=node.yaml_meta if node else None,
    )
    content = _company_markdown(yaml_meta)
    if node is None:
        node = WikiNode(
            workspace_id=source_document.workspace_id,
            owner_user_id=source_document.owner_user_id,
            title=title,
            type="company-profile",
            yaml_meta=yaml_meta,
            content_md=content,
        )
    else:
        node.yaml_meta = {**node.yaml_meta, **yaml_meta}
        node.content_md = node.content_md or content
        node.updated_at = utcnow()
    session.add(node)
    session.flush()
    return node


def _upsert_concept_node(
    session: Session,
    item: dict[str, object],
    metadata: dict[str, object],
    source_document: SourceDocument,
) -> WikiNode | None:
    title = _normalize_concept_title(item.get("title"))
    node_type = str(item.get("type") or "general-concept").strip()[:30]
    if not title:
        return None
    node = session.exec(
        select(WikiNode).where(WikiNode.workspace_id == source_document.workspace_id, WikiNode.title == title, WikiNode.type == node_type)
    ).first()
    if node is None and node_type == "company-executive-profile":
        node = session.exec(
            select(WikiNode).where(WikiNode.workspace_id == source_document.workspace_id, WikiNode.title == title, WikiNode.type == "general-concept")
        ).first()
        if node is not None:
            node.type = node_type
    elif node is None and node_type == "general-concept":
        existing_person = session.exec(
            select(WikiNode).where(
                WikiNode.workspace_id == source_document.workspace_id,
                WikiNode.title == title,
                WikiNode.type == "company-executive-profile",
            )
        ).first()
        if existing_person is not None:
            node = existing_person
            node_type = existing_person.type
    yaml_meta = _merge_source_ids(
        {
            "title": title,
            "type": node_type,
            "ticker": metadata.get("ticker"),
            "company_name": metadata.get("company_name"),
            "company_short_name": metadata.get("company_short_name"),
            "tags": ["概念", str(item.get("label") or "")],
            "analysis_status": "parsed",
            "description": item.get("description") or item.get("label") or "相关概念",
            **_item_metadata(item),
        },
        source_document,
        existing=node.yaml_meta if node else None,
    )
    content = _concept_markdown(yaml_meta)
    if node is None:
        node = WikiNode(
            workspace_id=source_document.workspace_id,
            owner_user_id=source_document.owner_user_id,
            title=title,
            type=node_type,
            yaml_meta=yaml_meta,
            content_md=content,
        )
    else:
        old_content = node.content_md
        old_meta = dict(node.yaml_meta or {})
        node.yaml_meta = {**old_meta, **yaml_meta}
        if _should_update_concept_content(old_content, old_meta, yaml_meta):
            node.content_md = content
        else:
            node.content_md = _append_uploaded_material_update(old_content, yaml_meta, source_document)
        node.updated_at = utcnow()
    session.add(node)
    session.flush()
    return node


def _ensure_edge(
    session: Session,
    source: WikiNode,
    target: WikiNode,
    relation_type: str,
    weight: float,
    metadata: dict[str, object],
) -> int:
    if source.id == target.id:
        return 0
    existing = session.exec(
        select(KnowledgeEdge).where(
            KnowledgeEdge.workspace_id == source.workspace_id,
            KnowledgeEdge.src_node_id == source.id,
            KnowledgeEdge.tgt_node_id == target.id,
            KnowledgeEdge.relation_type == relation_type,
        )
    ).first()
    if existing is not None:
        return 0
    session.add(
        KnowledgeEdge(
            workspace_id=source.workspace_id,
            owner_user_id=source.owner_user_id,
            src_node_id=source.id,
            tgt_node_id=target.id,
            relation_type=relation_type,
            weight=weight,
            edge_metadata=metadata,
        )
    )
    session.flush()
    return 1


def _extract_related_concepts(metadata: dict[str, object], markdown: str = "") -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    company_names = {
        str(metadata.get("company_name") or "").strip(),
        str(metadata.get("company_short_name") or "").strip(),
    }
    items.extend(_items_from_field(metadata, "executives", "company-executive-profile", "核心人物", "HAS_EXECUTIVE", 0.76)[:12])
    items.extend(_items_from_field(metadata, "key_personnel", "company-executive-profile", "核心人物", "HAS_EXECUTIVE", 0.74)[:8])
    for title in _ensure_list(metadata.get("related")):
        clean = _normalize_concept_title(title)
        if clean and clean not in company_names:
            items.append(
                {
                    "title": clean,
                    "type": "general-concept",
                    "label": "概念",
                    "field": "related",
                    "relation_type": "RELATED_CONCEPT",
                    "weight": 0.52,
                }
            )
    items.extend(_items_from_wikilinks(markdown, metadata))
    items.extend(_items_from_field(metadata, "business_segments", "company-finance-segment", "业务财务", "HAS_SEGMENT", 0.72)[:16])
    items.extend(_items_from_field(metadata, "risks", "company-risk-operation", "风险事件", "HAS_RISK", 0.7)[:16])
    items.extend(_items_from_field(metadata, "management_strategy", "company-strategy-goal", "战略目标", "HAS_STRATEGY", 0.64)[:16])
    items.extend(_items_from_field(metadata, "investment_view", "investment-insight", "概念", "HAS_INVESTMENT_CONCEPT", 0.58)[:12])
    items.extend(_items_from_field(metadata, "key_metrics", "company-finance-segment", "业务财务", "HAS_METRIC", 0.64)[:24])
    unique: dict[tuple[str, str], dict[str, object]] = {}
    for item in items:
        key = (str(item.get("title") or ""), str(item.get("type") or ""))
        if key[0]:
            unique.setdefault(key, item)
    return list(unique.values())[:96]


def _items_from_wikilinks(markdown: str, metadata: dict[str, object]) -> list[dict[str, object]]:
    current_title = str(metadata.get("title") or "").strip()
    company_names = {
        str(metadata.get("company_name") or "").strip(),
        str(metadata.get("company_short_name") or "").strip(),
    }
    executive_lookup = _named_item_lookup(metadata.get("executives"), metadata.get("key_personnel"))
    items: list[dict[str, object]] = []
    for title in re.findall(r"\[\[([^\]]+)]]", markdown or ""):
        clean = title.strip()
        if not clean or clean == current_title or clean in company_names:
            continue
        executive = executive_lookup.get(clean)
        if executive:
            items.append(
                {
                    "title": clean,
                    "type": "company-executive-profile",
                    "description": executive.get("description") or executive.get("role") or "核心人物",
                    "role": executive.get("role"),
                    "label": "核心人物",
                    "field": "content_links",
                    "relation_type": "HAS_EXECUTIVE",
                    "weight": 0.62,
                }
            )
            continue
        items.append(
            {
                "title": clean,
                "type": "general-concept",
                "label": "正文双链",
                "field": "content_links",
                "relation_type": "MENTIONS",
                "weight": 0.48,
            }
        )
    return items[:48]


def _named_item_lookup(*values: object) -> dict[str, dict[str, object]]:
    lookup: dict[str, dict[str, object]] = {}
    for value in values:
        for raw in _ensure_list(value):
            title, description = _concept_title_and_description(raw)
            if not title or title in lookup:
                continue
            item: dict[str, object] = {"description": description}
            if isinstance(raw, dict):
                for key in ("role", "position", "source"):
                    if raw.get(key):
                        item[key] = raw[key]
                if not item.get("role") and raw.get("position"):
                    item["role"] = raw["position"]
            lookup[title] = item
    return lookup


def _items_from_field(
    metadata: dict[str, object],
    field: str,
    node_type: str,
    label: str,
    relation_type: str,
    weight: float,
) -> list[dict[str, object]]:
    value = metadata.get(field)
    raw_items: list[object]
    if isinstance(value, dict):
        raw_items = [{"name": key, "description": item} for key, item in value.items()]
    else:
        raw_items = _ensure_list(value)
    items: list[dict[str, object]] = []
    for raw in raw_items:
        title, description = _concept_title_and_description(raw)
        if not title:
            continue
        items.append(
            {
                "title": title,
                "description": description,
                "type": node_type,
                "label": label,
                "field": field,
                "relation_type": relation_type,
                "weight": weight,
                **_item_metadata(raw if isinstance(raw, dict) else {}),
            }
        )
    return items


def _item_metadata(item: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in ("role", "position", "source"):
        value = item.get(key)
        if value:
            metadata[key] = value
    if "role" not in metadata and metadata.get("position"):
        metadata["role"] = metadata["position"]
    return metadata


def _concept_title_and_description(raw: object) -> tuple[str, str]:
    if isinstance(raw, dict):
        for key in ("name", "title", "metric", "segment", "risk", "goal", "topic"):
            if raw.get(key):
                title = _normalize_concept_title(raw[key])
                description = str(
                    raw.get("description")
                    or raw.get("summary")
                    or raw.get("role")
                    or raw.get("position")
                    or raw.get("value")
                    or ""
                ).strip()
                return title[:80], description[:180]
        if len(raw) == 1:
            key, value = next(iter(raw.items()))
            return _normalize_concept_title(key)[:80], str(value).strip()[:180]
        return "", ""
    title = _normalize_concept_title(raw)
    if not title:
        return "", ""
    title = title.replace("[[", "").replace("]]", "")
    return title[:80], ""


def _normalize_concept_title(value: object) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"\[\s*['\"]([^'\"]{1,120})['\"]\s*]", text)
    if match:
        return match.group(1).strip()
    return text


def _merge_named_items(primary: object, fallback: list[dict[str, object]]) -> list[object]:
    items: list[object] = []
    seen: set[str] = set()
    for raw in [*_ensure_list(primary), *fallback]:
        title, _ = _concept_title_and_description(raw)
        key = title.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(raw)
    return items[:24]


def _extract_executives(text: str) -> list[dict[str, object]]:
    people: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, role: str, source: str) -> None:
        clean_name = re.sub(r"\s+", "", name).strip("：:，,;；.。")
        clean_role = role.strip("：:，,;；.。")
        if not _looks_like_person_name(clean_name) or not clean_role:
            return
        for person in people:
            if person.get("name") != clean_name:
                continue
            existing_role = str(person.get("role") or "")
            if clean_role in existing_role:
                return
            if existing_role in clean_role:
                person["role"] = clean_role
                person["description"] = f"{clean_role}，从年报治理或联系人信息中抽取。"
                person["source"] = source
                return
        key = (clean_name, clean_role)
        if key in seen:
            return
        seen.add(key)
        people.append(
            {
                "name": clean_name,
                "role": clean_role,
                "description": f"{clean_role}，从年报治理或联系人信息中抽取。",
                "source": source,
            }
        )

    direct_patterns = [
        (r"公司的法定代表人\s*([一-龥·]{2,4})", "法定代表人"),
        (r"公司负责人\s*([一-龥·]{2,4})", "公司负责人"),
        (r"主管会计工作负责人\s*([一-龥·]{2,4})", "主管会计工作负责人"),
        (r"会计机构负责人[（(]会计主管人员[）)]\s*([一-龥·]{2,4})", "会计机构负责人"),
    ]
    for pattern, role in direct_patterns:
        for match in re.finditer(pattern, text):
            add(match.group(1), role, "deterministic-pattern")

    for match in re.finditer(r"董事会秘书\s*证券事务代表\s*姓名\s*([一-龥·]{2,4})\s+([一-龥·]{2,4})", text):
        add(match.group(1), "董事会秘书", "deterministic-contact-table")
        add(match.group(2), "证券事务代表", "deterministic-contact-table")

    roles = [
        "副董事长、总经理",
        "董事长（离任）",
        "董事、董事会秘书",
        "副董事长",
        "董事长",
        "董事会秘书",
        "财务总监",
        "总经理",
        "总裁",
    ]
    for line in text.splitlines():
        if "联系人和联系方式" in line or "姓名" in line:
            continue
        for role in roles:
            pattern = rf"([一-龥](?:[ \t]*[一-龥]){{1,3}})[ \t]+{re.escape(role)}"
            for match in re.finditer(pattern, line):
                add(match.group(1), role, "deterministic-executive-table")
                if len(people) >= 24:
                    break
            if len(people) >= 24:
                break
        if len(people) >= 24:
            break

    return people[:24]


def _looks_like_person_name(value: str) -> bool:
    if not re.fullmatch(r"[\u4e00-\u9fff·]{2,4}", value):
        return False
    blocked_fragments = ("公司", "有限", "责任", "集团", "发展", "商城", "房产", "及会", "会议")
    if value.endswith("及") or any(fragment in value for fragment in blocked_fragments):
        return False
    blocked = {
        "公司",
        "报告",
        "董事",
        "监事",
        "年度",
        "年末",
        "年初",
        "报酬",
        "职权",
        "担任",
        "同时",
        "不存在",
        "获取",
        "适用",
        "治理",
        "管理",
        "人员",
        "联系人",
        "联系方式",
    }
    return value not in blocked


def _merge_source_ids(
    metadata: dict[str, object],
    source_document: SourceDocument,
    existing: dict[str, object] | None = None,
) -> dict[str, object]:
    existing = existing or {}
    source_ids = [str(item) for item in _ensure_list(existing.get("source_document_ids"))]
    if str(source_document.id) not in source_ids:
        source_ids.append(str(source_document.id))
    return {
        **existing,
        **metadata,
        "source_document_ids": source_ids,
        "folder_path": metadata.get("folder_path") or source_document.document_metadata.get("folder_path") or existing.get("folder_path"),
    }


def _company_markdown(metadata: dict[str, object]) -> str:
    title = str(metadata.get("company_short_name") or metadata.get("company_name") or metadata.get("title") or "公司")
    ticker = metadata.get("ticker") or "未披露"
    company_name = metadata.get("company_name") or title
    return f"# {title}\n\n## 公司概览\n{company_name}（{ticker}）是当前知识库中的公司条目，汇总来自已上传年报、公告和研究材料的结构化信息。\n\n## 相关材料\n后续解析同公司材料时，本条目会继续聚合对应的业务、财务、风险和战略概念。"


def _concept_markdown(metadata: dict[str, object]) -> str:
    title = str(metadata.get("title") or "概念")
    description = str(metadata.get("description") or "该概念由上传材料解析生成。")
    company = metadata.get("company_short_name") or metadata.get("company_name") or "相关公司"
    if metadata.get("type") == "company-executive-profile":
        role = str(metadata.get("role") or metadata.get("position") or description or "待补充")
        return (
            f"# {title}\n\n"
            f"## 人物概览\n{description}\n\n"
            f"## 当前职务\n{role}\n\n"
            "## 履历与证据\n"
            "该人物履历来自已上传材料抽取。需要更完整履历时，可接入公开资料搜索并把来源保存到证据链。\n\n"
            f"## 关联公司\n[[{company}]]"
        )
    return f"# {title}\n\n## 概念说明\n{description}\n\n## 关联对象\n该概念当前关联到 [[{company}]] 的已解析材料。"


def _should_replace_generated_content(content: str | None) -> bool:
    if not content:
        return True
    generated_markers = (
        "## 概念说明\n正文双链",
        "## 概念说明\n相关概念",
        "## 概念说明\n概念",
        "该概念当前关联到",
        "该概念由上传材料解析生成",
    )
    return any(marker in content for marker in generated_markers)


def _should_update_concept_content(
    content: str | None,
    old_metadata: dict[str, object] | None,
    new_metadata: dict[str, object],
) -> bool:
    if _should_replace_generated_content(content):
        return True
    current = content or ""
    old_description = _clean_description((old_metadata or {}).get("description"))
    new_description = _clean_description(new_metadata.get("description"))
    if _is_placeholder_description(new_description):
        return False
    if new_description in current:
        return False
    if _is_placeholder_description(old_description):
        return True
    if _has_user_or_web_enriched_body(current, old_metadata or {}):
        return False
    return _looks_like_generated_concept_page(current) and len(new_description) >= max(24, len(old_description) + 16)


def _append_uploaded_material_update(
    content: str | None,
    metadata: dict[str, object],
    source_document: SourceDocument,
) -> str | None:
    if not content:
        return content
    description = _clean_description(metadata.get("description"))
    if _is_placeholder_description(description) or description in content:
        return content
    source_label = source_document.filename or "上传材料"
    if source_label in content and description[:40] in content:
        return content
    line = f"- {source_label}：{description}"
    if "## 上传材料更新" in content:
        return f"{content.rstrip()}\n{line}"
    return f"{content.rstrip()}\n\n## 上传材料更新\n{line}"


def _clean_description(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_placeholder_description(value: str) -> bool:
    if not value:
        return True
    placeholders = {
        "正文双链",
        "相关概念",
        "概念",
        "核心人物",
        "业务财务",
        "战略目标",
        "风险事件",
        "该概念由上传材料解析生成。",
    }
    return value in placeholders or value.endswith("相关词条")


def _has_user_or_web_enriched_body(content: str, metadata: dict[str, object]) -> bool:
    if metadata.get("web_enrichment") or "## 联网来源" in content or "## 联网补充材料" in content:
        return len(content.strip()) >= 600
    return len(content.strip()) >= 1200


def _looks_like_generated_concept_page(content: str) -> bool:
    markers = ("## 概念说明", "## 人物概览", "## 当前职务", "## 关联对象", "## 关联公司")
    return any(marker in content for marker in markers)


def _ensure_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _persist_source_spans(
    session: Session,
    source_document: SourceDocument,
    parsed_artifact: ParsedArtifact,
    spans: list[dict[str, object]],
    workspace_id: UUID | None = None,
    owner_user_id: UUID | None = None,
) -> list[SourceSpan]:
    records: list[SourceSpan] = []
    char_cursor = 0
    for span in spans[:1000]:
        text = str(span.get("text") or "")
        if not text.strip():
            continue
        char_start = char_cursor
        char_end = char_start + len(text)
        char_cursor = char_end + 2
        record = SourceSpan(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            source_document_id=source_document.id,
            parsed_artifact_id=parsed_artifact.id,
            span_type=str(span.get("type") or "paragraph"),
            locator=span.get("locator") if isinstance(span.get("locator"), dict) else {},
            text=text,
            char_start=char_start,
            char_end=char_end,
            confidence=float(span.get("confidence") or 1.0),
        )
        session.add(record)
        records.append(record)
    session.commit()
    for record in records:
        session.refresh(record)
    return records


def _persist_wiki_evidence_links(
    session: Session,
    node: WikiNode,
    source_spans: list[SourceSpan],
    workspace_id: UUID | None = None,
    owner_user_id: UUID | None = None,
) -> None:
    for span in source_spans[:200]:
        quote = span.text[:500]
        session.add(
            EvidenceLink(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                target_type="wiki_node",
                target_id=str(node.id),
                source_span_id=span.id,
                quote=quote,
                relevance_score=0.5,
                evidence_metadata={"strategy": "parser_span_initial_link"},
            )
        )


def _infer_domain_pack_candidates(metadata: dict[str, object], text: str) -> list[dict[str, object]]:
    haystack = f"{metadata.get('title') or ''}\n{metadata.get('type') or ''}\n{text[:4000]}"
    candidates = [{"slug": "general-document", "confidence": 1.0, "reason": "default"}]
    if any(keyword in haystack for keyword in ("年度报告", "年报", "董事会报告", "管理层讨论与分析")):
        candidates.append({"slug": "a-share-annual-report", "confidence": 0.9, "reason": "annual report keywords"})
    if any(keyword in haystack for keyword in ("ROIC", "自由现金流", "护城河", "分红", "资本配置")):
        candidates.append({"slug": "value-investing", "confidence": 0.75, "reason": "value investing keywords"})
    if any(keyword in haystack for keyword in ("诉讼", "处罚", "监管", "合规", "重大风险", "内控")):
        candidates.append({"slug": "risk-compliance", "confidence": 0.8, "reason": "risk keywords"})
    if any(keyword in haystack for keyword in ("公告", "新闻", "媒体", "报道")):
        candidates.append({"slug": "company-news", "confidence": 0.65, "reason": "news keywords"})
    return candidates


def _normalize_folder_path(value: object | None) -> str:
    if value is None:
        return ""
    parts = [part.strip(" .") for part in str(value).replace("\\", "/").split("/") if part.strip(" .")]
    safe_parts = ["".join(char for char in part if char not in '<>:"|?*')[:80] for part in parts[:8]]
    return "/".join(part for part in safe_parts if part)


def _completion_message(metadata: dict[str, object]) -> str:
    memory_sync = metadata.get("memory_sync")
    if isinstance(memory_sync, dict) and memory_sync.get("status") == "failed":
        error = str(memory_sync.get("error") or "unknown error")
        return f"Document parsed, but long-term memory sync failed: {error[:160]}"
    llm_extraction = metadata.get("llm_extraction")
    if isinstance(llm_extraction, dict) and llm_extraction.get("status") == "failed":
        error = str(llm_extraction.get("error") or "unknown error")
        return f"Document parsed with LLM fallback: {error[:160]}"
    return "Document parsed and stored"
