from typing import Any
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import ExternalMemoryMapping, WikiNode
from app.scripts.rebuild_cognee_memory import _doc_hash_for_node, _is_memory_backed_node, _memory_resource_for_node
from app.services.long_term_memory import (
    ExternalMemoryReference,
    LongTermMemoryProvider,
    MemoryScope,
    ProviderResult,
)
from app.services.memory import COGNEE_LLM_DEFAULTS, MemoryClient, _dataset_name, _prepare_cognee_environment


def test_prepare_cognee_environment_does_not_read_api_keys_from_environment() -> None:
    env = {
        "api_key": "legacy-shared-key",
        "LLM_API_KEY": "legacy-shared-key",
        "LLM_MODEL": "custom/model",
        "ENABLE_BACKEND_ACCESS_CONTROL": "false",
    }

    _prepare_cognee_environment(env)

    assert "LLM_API_KEY" not in env
    assert "api_key" not in env
    assert env["LLM_MODEL"] == "custom/model"
    assert env["LLM_PROVIDER"] == COGNEE_LLM_DEFAULTS["LLM_PROVIDER"]
    assert env["LLM_ENDPOINT"] == "https://api.minimaxi.com/v1"
    assert env["OPENAI_API_BASE"] == "https://api.minimaxi.com/v1"
    assert env["EMBEDDING_PROVIDER"] == "fastembed"
    assert env["EMBEDDING_MODEL"] == "jinaai/jina-embeddings-v2-base-zh"
    assert env["EMBEDDING_DIMENSIONS"] == "768"
    assert env["EMBEDDING_BATCH_SIZE"] == "16"
    assert env["TELEMETRY_DISABLED"] == "true"
    assert env["ENABLE_BACKEND_ACCESS_CONTROL"] == "true"


def test_prepare_cognee_environment_sets_persistent_fastembed_cache() -> None:
    env = {"STORAGE_DIR": "/app/storage"}

    _prepare_cognee_environment(env)

    assert env["HF_HOME"] == "/app/storage/huggingface"
    assert env["FASTEMBED_CACHE_PATH"] == "/app/storage/fastembed"


def test_dataset_name_requires_workspace_and_preserves_folder_scope() -> None:
    workspace_id = uuid4()

    assert _dataset_name({"workspace_id": workspace_id, "folder_path": "reports/annual"}) == (
        f"value_invest_wiki_ws_{str(workspace_id).replace('-', '_')}_reports_annual"
    )
    with pytest.raises(ValueError, match="workspace_id is required"):
        _dataset_name({"folder_path": "reports/annual"})


def test_rebuild_memory_resource_prefers_source_document_scope() -> None:
    workspace_id = uuid4()
    source_document_id = uuid4()
    source_node = WikiNode(
        workspace_id=workspace_id,
        title="source backed node",
        type="annual-report",
        yaml_meta={"source_document_id": str(source_document_id)},
        content_md="annual report",
    )
    standalone_node = WikiNode(
        workspace_id=workspace_id,
        title="standalone concept",
        type="general-concept",
        yaml_meta={},
        content_md="concept",
    )

    assert _memory_resource_for_node(source_node) == ("source_document", str(source_document_id))
    assert _memory_resource_for_node(standalone_node) == ("wiki_node", str(standalone_node.id))
    assert _is_memory_backed_node(source_node) is True
    assert _is_memory_backed_node(standalone_node) is False
    assert len(_doc_hash_for_node(standalone_node)) == 64


class FakeLongTermMemoryProvider(LongTermMemoryProvider):
    name = "fake"

    def __init__(self) -> None:
        self.deleted: list[ExternalMemoryReference] = []

    async def remember(
        self,
        content: str,
        dataset_name: str,
        scope: MemoryScope,
        metadata: dict[str, Any],
    ) -> ProviderResult:
        return ProviderResult(
            ok=True,
            backend="fake.remember",
            dataset_name=dataset_name,
            dataset_id=str(uuid4()),
            data_ids=[str(uuid4())],
            user_id=str(uuid4()),
        )

    async def recall(
        self,
        query: str,
        top_k: int,
        dataset_names: list[str],
        scope: MemoryScope,
    ) -> ProviderResult:
        return ProviderResult(ok=True, backend="fake.recall", items=[])

    async def forget(
        self,
        reference: ExternalMemoryReference,
        scope: MemoryScope,
    ) -> ProviderResult:
        self.deleted.append(reference)
        return ProviderResult(ok=True, backend="fake.delete-hard")


class FailingRememberProvider(FakeLongTermMemoryProvider):
    async def remember(
        self,
        content: str,
        dataset_name: str,
        scope: MemoryScope,
        metadata: dict[str, Any],
    ) -> ProviderResult:
        return ProviderResult(ok=False, backend="fake.remember", dataset_name=dataset_name, error="provider failed")


@pytest.mark.asyncio
async def test_external_mapping_drives_workspace_scoped_hard_delete() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    source_document_id = uuid4()
    provider = FakeLongTermMemoryProvider()

    with Session(engine) as session:
        client = MemoryClient(session=session, provider=provider)
        remembered = await client.remember(
            content="durable knowledge",
            metadata={"workspace_id": str(workspace_id), "source_document_id": str(source_document_id)},
            doc_hash="a" * 64,
            local_resource_id=str(source_document_id),
        )
        assert remembered["ok"] is True

        mapping = session.exec(select(ExternalMemoryMapping)).one()
        assert mapping.workspace_id == workspace_id
        assert mapping.sync_status == "synced"
        assert mapping.external_dataset_id
        assert mapping.external_data_id

        wrong_workspace = await client.forget(
            workspace_id=other_workspace_id,
            doc_hash="a" * 64,
        )
        assert wrong_workspace["ok"] is False
        assert provider.deleted == []

        deleted = await client.forget(
            workspace_id=workspace_id,
            local_resource_type="source_document",
            local_resource_id=str(source_document_id),
        )
        assert deleted["ok"] is True
        assert len(provider.deleted) == 1
        session.refresh(mapping)
        assert mapping.sync_status == "deleted"


@pytest.mark.asyncio
async def test_provider_failure_is_persisted_as_failed_sync_status() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    workspace_id = uuid4()

    with Session(engine) as session:
        result = await MemoryClient(session=session, provider=FailingRememberProvider()).remember(
            content="knowledge that cannot be synced",
            metadata={"workspace_id": str(workspace_id)},
            doc_hash="b" * 64,
        )

        assert result["ok"] is False
        mapping = session.exec(select(ExternalMemoryMapping)).one()
        assert mapping.sync_status == "failed"
        assert mapping.last_error == "provider failed"
