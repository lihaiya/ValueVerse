from typing import Any

from sqlmodel import Session, select

from app.models import EvidenceLink, KnowledgeEdge, ParseTask, ParsedArtifact, SourceDocument, SourceSpan, WikiNode
from app.services.storage import get_object_storage


def collect_source_document_related(session: Session, source_document: SourceDocument) -> dict[str, list[Any]]:
    source_document_id = source_document.id
    refs = [source_document.storage_uri]
    wiki_nodes = [
        node
        for node in session.exec(select(WikiNode).where(WikiNode.workspace_id == source_document.workspace_id)).all()
        if _node_belongs_to_source(node, source_document)
    ]
    wiki_node_ids = {node.id for node in wiki_nodes}
    wiki_node_id_strings = {str(node.id) for node in wiki_nodes}
    refs.extend(node.raw_content_ref for node in wiki_nodes if node.raw_content_ref)
    spans = session.exec(
        select(SourceSpan).where(
            SourceSpan.workspace_id == source_document.workspace_id,
            SourceSpan.source_document_id == source_document_id,
        )
    ).all()
    span_ids = {span.id for span in spans}
    artifacts = session.exec(
        select(ParsedArtifact).where(
            ParsedArtifact.workspace_id == source_document.workspace_id,
            ParsedArtifact.source_document_id == source_document_id,
        )
    ).all()
    parse_task_id = str(source_document.document_metadata.get("parse_task_id") or "")
    tasks = [
        task
        for task in session.exec(select(ParseTask).where(ParseTask.workspace_id == source_document.workspace_id)).all()
        if task.raw_content_ref == source_document.storage_uri or str(task.id) == parse_task_id
    ]
    evidence_links = [
        link
        for link in session.exec(select(EvidenceLink).where(EvidenceLink.workspace_id == source_document.workspace_id)).all()
        if link.source_span_id in span_ids
        or (link.target_type == "wiki_node" and link.target_id in wiki_node_id_strings)
    ]
    knowledge_edges = [
        edge
        for edge in session.exec(select(KnowledgeEdge).where(KnowledgeEdge.workspace_id == source_document.workspace_id)).all()
        if edge.src_node_id in wiki_node_ids or edge.tgt_node_id in wiki_node_ids
    ]
    return {
        "refs": refs,
        "evidence_links": evidence_links,
        "knowledge_edges": knowledge_edges,
        "spans": spans,
        "artifacts": artifacts,
        "tasks": tasks,
        "wiki_nodes": wiki_nodes,
    }


def delete_source_document_records(
    session: Session,
    source_document: SourceDocument,
    delete_source_file: bool = False,
) -> dict[str, int | list[str]]:
    related = collect_source_document_related(session, source_document)
    doc_hashes = [node.cognee_doc_hash for node in related["wiki_nodes"] if node.cognee_doc_hash]
    pending_hash = source_document.document_metadata.get("pending_cognee_doc_hash")
    if pending_hash:
        doc_hashes.append(str(pending_hash))
    deleted: dict[str, int | list[str]] = {
        "evidence_links": len(related["evidence_links"]),
        "knowledge_edges": len(related["knowledge_edges"]),
        "source_spans": len(related["spans"]),
        "parsed_artifacts": len(related["artifacts"]),
        "parse_tasks": len(related["tasks"]),
        "wiki_nodes": len(related["wiki_nodes"]),
        "source_documents": 1,
        "source_files": 0,
        "doc_hashes": doc_hashes,
    }
    for key in ("evidence_links", "knowledge_edges", "spans", "artifacts", "tasks", "wiki_nodes"):
        for record in related[key]:
            session.delete(record)
        if related[key]:
            session.flush()
    session.delete(source_document)
    session.flush()
    if delete_source_file:
        deleted["source_files"] = delete_local_source_files(related["refs"])
    return deleted


def delete_local_source_files(refs: list[str]) -> int:
    storage = get_object_storage()
    root = storage.root.resolve()
    deleted = 0
    for ref in set(refs):
        path = storage.resolve(ref).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.exists() and path.is_file():
            path.unlink()
            deleted += 1
    return deleted


def _node_belongs_to_source(node: WikiNode, source_document: SourceDocument) -> bool:
    metadata = node.yaml_meta or {}
    source_id = str(source_document.id)
    if node.raw_content_ref == source_document.storage_uri:
        return True
    if str(metadata.get("source_document_id") or "") == source_id:
        return True
    source_ids = metadata.get("source_document_ids")
    if not isinstance(source_ids, list):
        return False
    normalized = {str(item) for item in source_ids}
    return source_id in normalized and len(normalized) <= 1
