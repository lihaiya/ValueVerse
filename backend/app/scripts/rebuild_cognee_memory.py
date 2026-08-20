import argparse
import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.db.session import create_db_and_tables, get_engine
from app.models import ExternalMemoryMapping, WikiNode, utcnow
from app.services.memory import MemoryClient, _prepare_cognee_environment

HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class RebuildSummary:
    total_nodes: int
    attempted: int = 0
    synced: int = 0
    failed: int = 0
    skipped: int = 0
    pruned: bool = False
    cleared_mappings: int = 0


async def rebuild_cognee_memory_index(
    session: Session,
    *,
    force: bool = False,
    prune: bool = True,
    limit: int | None = None,
    include_generated: bool = False,
) -> RebuildSummary:
    nodes = _visible_nodes(session, limit=limit, include_generated=include_generated)
    summary = RebuildSummary(total_nodes=len(nodes))
    if not nodes:
        return summary
    if not force:
        return summary

    if prune:
        await _prune_cognee()
        summary.pruned = True

        result = session.exec(delete(ExternalMemoryMapping).where(ExternalMemoryMapping.provider == "cognee"))
        summary.cleared_mappings = int(result.rowcount or 0)
        session.commit()

    memory_client = MemoryClient(session)
    for node in nodes:
        if not (node.content_md or "").strip():
            summary.skipped += 1
            continue

        metadata = _metadata_for_node(node)
        resource_type, resource_id = _memory_resource_for_node(node)
        doc_hash = _doc_hash_for_node(node)
        if not prune and _has_synced_mapping(session, node, resource_type, resource_id, doc_hash):
            summary.skipped += 1
            continue
        result = await memory_client.remember(
            content=_memory_content_for_node(node),
            metadata=metadata,
            doc_hash=doc_hash,
            local_resource_type=resource_type,
            local_resource_id=resource_id,
        )
        summary.attempted += 1
        node_metadata = dict(node.yaml_meta or {})
        node_metadata["memory_sync"] = {
            "status": result.get("sync_status", "failed"),
            "provider": result.get("backend"),
            "error": result.get("error"),
            "rebuilt_at": utcnow().isoformat(),
        }
        node.yaml_meta = node_metadata
        node.cognee_doc_hash = doc_hash
        node.updated_at = utcnow()
        session.add(node)
        session.commit()
        if result.get("ok"):
            summary.synced += 1
        else:
            summary.failed += 1

    return summary


def _has_synced_mapping(
    session: Session,
    node: WikiNode,
    resource_type: str,
    resource_id: str,
    doc_hash: str,
) -> bool:
    if node.workspace_id is None:
        return False
    return (
        session.exec(
            select(ExternalMemoryMapping).where(
                ExternalMemoryMapping.workspace_id == node.workspace_id,
                ExternalMemoryMapping.provider == "cognee",
                ExternalMemoryMapping.resource_type == resource_type,
                ExternalMemoryMapping.local_id == resource_id,
                ExternalMemoryMapping.doc_hash == doc_hash,
                ExternalMemoryMapping.sync_status == "synced",
            )
        ).first()
        is not None
    )


def _visible_nodes(session: Session, limit: int | None = None, include_generated: bool = False) -> list[WikiNode]:
    stmt = select(WikiNode).order_by(col(WikiNode.updated_at).asc())
    if limit is not None:
        stmt = stmt.limit(max(0, limit))
    nodes = session.exec(stmt).all()
    return [node for node in nodes if _is_visible_node(node) and (include_generated or _is_memory_backed_node(node))]


def _is_visible_node(node: WikiNode) -> bool:
    status = str((node.yaml_meta or {}).get("analysis_status") or "").strip().lower()
    return status not in {"deleted", "deprecated"}


def _is_memory_backed_node(node: WikiNode) -> bool:
    metadata = node.yaml_meta or {}
    return _uuid_or_none(metadata.get("source_document_id")) is not None


async def _prune_cognee() -> None:
    _prepare_cognee_environment()
    import cognee

    await cognee.prune.prune_data()
    await cognee.prune.prune_system(graph=True, vector=True, metadata=True, cache=True)


def _metadata_for_node(node: WikiNode) -> dict[str, Any]:
    metadata = dict(node.yaml_meta or {})
    metadata["title"] = node.title
    metadata["type"] = node.type
    if node.workspace_id is not None:
        metadata["workspace_id"] = str(node.workspace_id)
    if node.owner_user_id is not None:
        metadata["owner_user_id"] = str(node.owner_user_id)
    return metadata


def _memory_resource_for_node(node: WikiNode) -> tuple[str, str]:
    source_document_id = _source_document_id_for_node(node)
    if source_document_id is not None:
        return "source_document", str(source_document_id)
    return "wiki_node", str(node.id)


def _source_document_id_for_node(node: WikiNode) -> UUID | None:
    metadata = node.yaml_meta or {}
    direct = _uuid_or_none(metadata.get("source_document_id"))
    if direct is not None:
        return direct
    source_ids = metadata.get("source_document_ids")
    if isinstance(source_ids, list) and source_ids:
        return _uuid_or_none(source_ids[0])
    return None


def _doc_hash_for_node(node: WikiNode) -> str:
    existing = str(node.cognee_doc_hash or "").strip().lower()
    if HEX_SHA256.fullmatch(existing):
        return existing
    payload = f"{node.id}\n{node.title}\n{node.type}\n{node.content_md or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _memory_content_for_node(node: WikiNode) -> str:
    metadata = node.yaml_meta or {}
    header = [
        f"title: {node.title}",
        f"type: {node.type}",
    ]
    for key in ("company_short_name", "company_name", "ticker", "report_year"):
        value = metadata.get(key)
        if value:
            header.append(f"{key}: {value}")
    return "\n".join(header) + "\n\n" + (node.content_md or "")


def _uuid_or_none(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError, AttributeError):
        return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild Cognee long-term memory from Wiki nodes.")
    parser.add_argument("--force", action="store_true", help="Actually prune Cognee and rebuild the index.")
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Resume without pruning Cognee storage or clearing synced mappings.",
    )
    parser.add_argument(
        "--include-generated",
        action="store_true",
        help="Also index generated company, concept, person, risk, and metric Wiki nodes.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit nodes for a smoke test.")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    create_db_and_tables()
    with Session(get_engine()) as session:
        summary = await rebuild_cognee_memory_index(
            session,
            force=args.force,
            prune=not args.no_prune,
            limit=args.limit,
            include_generated=args.include_generated,
        )
    mode = "executed" if args.force else "dry-run"
    print(
        "Cognee memory rebuild "
        f"{mode}: total_nodes={summary.total_nodes}, attempted={summary.attempted}, "
        f"synced={summary.synced}, failed={summary.failed}, skipped={summary.skipped}, "
        f"pruned={summary.pruned}, cleared_mappings={summary.cleared_mappings}"
    )
    if summary.total_nodes and not args.force:
        print("Run again with --force to prune Cognee storage and rebuild the external memory index.")


if __name__ == "__main__":
    asyncio.run(_main())
