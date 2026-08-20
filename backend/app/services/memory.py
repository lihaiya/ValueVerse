import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from importlib import import_module
from typing import Any
from uuid import UUID

from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.models import ExternalMemoryMapping, utcnow
from app.services.llm_factory import LLMFactory
from app.services.long_term_memory import (
    CogneeLongTermMemoryProvider,
    DisabledLongTermMemoryProvider,
    ExternalMemoryReference,
    LongTermMemoryProvider,
    MemoryScope,
    ProviderResult,
    UnavailableLongTermMemoryProvider,
)

COGNEE_DATASET_NAME = "value_invest_wiki"
COGNEE_LLM_DEFAULTS = {
    "LLM_PROVIDER": "custom",
    "LLM_MODEL": "openai/MiniMax-M3",
    "LLM_ENDPOINT": "https://api.minimaxi.com/v1",
    "OPENAI_API_BASE": "https://api.minimaxi.com/v1",
    "OPENAI_BASE_URL": "https://api.minimaxi.com/v1",
    "LLM_INSTRUCTOR_MODE": "json_mode",
    "LLM_MAX_COMPLETION_TOKENS": "8192",
    "EMBEDDING_PROVIDER": "fastembed",
    "EMBEDDING_MODEL": "jinaai/jina-embeddings-v2-base-zh",
    "EMBEDDING_DIMENSIONS": "768",
    "EMBEDDING_MAX_COMPLETION_TOKENS": "8192",
    "EMBEDDING_BATCH_SIZE": "16",
    "TOKENIZERS_PARALLELISM": "false",
    "TELEMETRY_DISABLED": "true",
}
COGNEE_CACHE_ENV_DEFAULTS = {
    "HF_HOME": "huggingface",
    "FASTEMBED_CACHE_PATH": "fastembed",
}
COGNEE_PROVIDER_ALIASES = {
    "minimax": "custom",
    "openai": "openai",
    "custom_api": "custom",
    "ollama": "ollama",
}
COGNEE_RUNTIME_ENV_KEYS = {
    "LLM_API_KEY",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_ENDPOINT",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
}
_COGNEE_RUNTIME_LOCK = asyncio.Lock()


class MemoryClient:
    """Coordinates a long-term memory provider with durable external ID mappings."""

    def __init__(
        self,
        session: Session | None = None,
        provider: LongTermMemoryProvider | None = None,
        owner_user_id: UUID | str | None = None,
    ) -> None:
        self.settings = get_settings()
        self.session = session
        self.owner_user_id = _uuid_or_none(owner_user_id)
        self.provider = provider or _load_provider(self.settings.cognee_enabled)

    async def remember(
        self,
        content: str,
        metadata: dict[str, Any],
        doc_hash: str,
        local_resource_type: str = "source_document",
        local_resource_id: str | None = None,
    ) -> dict[str, Any]:
        scope = _scope(metadata.get("workspace_id"))
        if scope is None:
            return _failure("memory-scope", doc_hash, "workspace_id is required for long-term memory")
        dataset_name = _dataset_name(metadata)
        if isinstance(self.provider, DisabledLongTermMemoryProvider) and not isinstance(
            self.provider, UnavailableLongTermMemoryProvider
        ):
            return {
                "ok": True,
                "backend": "local-fallback",
                "doc_hash": doc_hash,
                "dataset_name": dataset_name,
                "sync_status": "disabled",
            }
        if self.session is None:
            return _failure("memory-mapping", doc_hash, "database session is required to persist external memory IDs")

        local_id = str(local_resource_id or metadata.get("source_document_id") or doc_hash)
        owner_user_id = _uuid_or_none(metadata.get("owner_user_id"))
        mapping = self._get_or_create_mapping(
            scope=scope,
            owner_user_id=owner_user_id,
            resource_type=local_resource_type,
            local_id=local_id,
            doc_hash=doc_hash,
            dataset_name=dataset_name,
        )
        mapping.sync_status = "syncing"
        mapping.attempt_count += 1
        mapping.last_error = None
        mapping.updated_at = utcnow()
        self.session.add(mapping)
        self.session.commit()

        try:
            async with self._cognee_runtime(scope.workspace_id, metadata.get("owner_user_id")):
                result = await self.provider.remember(content, dataset_name, scope, metadata)
        except Exception as exc:
            result = ProviderResult(
                ok=False,
                backend=f"{self.provider.name}.runtime",
                dataset_name=dataset_name,
                error=str(exc).strip()[:2000] or exc.__class__.__name__,
            )
        self._apply_provider_result(mapping, result)
        return _result_dict(result, doc_hash, mapping.sync_status)

    async def recall(
        self,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        filters = filters or {}
        scope = _scope(filters.get("workspace_id"))
        if scope is None:
            return {"ok": False, "backend": "memory-scope", "items": [], "error": "workspace_id is required"}
        dataset_names = self._recall_dataset_names(scope, filters)
        try:
            async with self._cognee_runtime(scope.workspace_id, filters.get("owner_user_id")):
                result = await self.provider.recall(query, top_k, dataset_names, scope)
        except Exception as exc:
            result = ProviderResult(
                ok=False,
                backend=f"{self.provider.name}.runtime",
                error=str(exc).strip()[:2000] or exc.__class__.__name__,
            )
        return {
            "ok": result.ok,
            "backend": result.backend,
            "items": result.items,
            "error": result.error,
        }

    async def forget(
        self,
        workspace_id: UUID | str | None,
        doc_hash: str | None = None,
        entity_urn: str | None = None,
        local_resource_type: str | None = None,
        local_resource_id: str | None = None,
    ) -> dict[str, Any]:
        scope = _scope(workspace_id)
        identifier = {
            "workspace_id": str(workspace_id or ""),
            "doc_hash": doc_hash,
            "entity_urn": entity_urn,
            "local_resource_type": local_resource_type,
            "local_resource_id": local_resource_id,
        }
        if scope is None:
            return {"ok": False, "backend": "memory-scope", "identifier": identifier, "error": "workspace_id is required"}
        if isinstance(self.provider, DisabledLongTermMemoryProvider) and not isinstance(
            self.provider, UnavailableLongTermMemoryProvider
        ):
            return {"ok": True, "backend": "local-fallback", "identifier": identifier, "deleted": 0}
        if self.session is None:
            return {
                "ok": False,
                "backend": "memory-mapping",
                "identifier": identifier,
                "error": "database session is required to resolve external memory IDs",
            }

        mappings = self._find_mappings(
            scope=scope,
            doc_hash=doc_hash,
            entity_urn=entity_urn,
            local_resource_type=local_resource_type,
            local_resource_id=local_resource_id,
        )
        if not mappings:
            if local_resource_type and local_resource_id:
                return {
                    "ok": True,
                    "backend": "memory-mapping-empty",
                    "identifier": identifier,
                    "deleted": 0,
                }
            return {
                "ok": False,
                "backend": "memory-mapping-missing",
                "identifier": identifier,
                "deleted": 0,
                "error": "no external memory mapping found in the active workspace",
            }

        results: list[dict[str, Any]] = []
        for mapping in mappings:
            mapping.sync_status = "deleting"
            mapping.attempt_count += 1
            mapping.last_error = None
            mapping.updated_at = utcnow()
            self.session.add(mapping)
        self.session.commit()

        for mapping in mappings:
            if not mapping.external_dataset_id or not mapping.external_data_id:
                result = ProviderResult(
                    ok=False,
                    backend=f"{self.provider.name}.delete",
                    error="external dataset_id/data_id is missing",
                )
            else:
                try:
                    async with self._cognee_runtime(scope.workspace_id, self.owner_user_id or mapping.owner_user_id):
                        result = await self.provider.forget(
                            ExternalMemoryReference(
                                dataset_name=mapping.dataset_name,
                                dataset_id=mapping.external_dataset_id,
                                data_id=mapping.external_data_id,
                                user_id=mapping.external_user_id,
                            ),
                            scope,
                        )
                except Exception as exc:
                    result = ProviderResult(
                        ok=False,
                        backend=f"{self.provider.name}.runtime",
                        error=str(exc).strip()[:2000] or exc.__class__.__name__,
                    )
            if result.ok:
                mapping.sync_status = "deleted"
                mapping.deleted_at = utcnow()
                mapping.last_error = None
            else:
                mapping.sync_status = "delete_failed"
                mapping.last_error = result.error
            mapping.updated_at = utcnow()
            self.session.add(mapping)
            results.append(
                {
                    "ok": result.ok,
                    "backend": result.backend,
                    "mapping_id": str(mapping.id),
                    "external_dataset_id": mapping.external_dataset_id,
                    "external_data_id": mapping.external_data_id,
                    "error": result.error,
                }
            )
        self.session.commit()
        ok = all(item["ok"] for item in results)
        return {
            "ok": ok,
            "backend": f"{self.provider.name}.delete-hard",
            "identifier": identifier,
            "deleted": sum(1 for item in results if item["ok"]),
            "results": results,
            "error": None if ok else "one or more external memory records could not be deleted",
        }

    async def improve(
        self,
        workspace_id: UUID | str | None,
        local_resource_id: str,
        doc_hash: str | None,
        field: str,
        correction: Any,
        reason: str | None,
        owner_user_id: UUID | str | None = None,
    ) -> dict[str, Any]:
        content = (
            "Wiki memory correction\n"
            f"source_doc_hash: {doc_hash or 'N/A'}\n"
            f"field: {field}\n"
            f"reason: {reason or 'N/A'}\n"
            f"correction: {correction}"
        )
        correction_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return await self.remember(
            content=content,
            metadata={
                "workspace_id": str(workspace_id or ""),
                "owner_user_id": str(owner_user_id or ""),
                "type": "memory-correction",
            },
            doc_hash=correction_hash,
            local_resource_type="wiki_correction",
            local_resource_id=local_resource_id,
        )

    async def forget_workspace(self, workspace_id: UUID | str | None) -> dict[str, Any]:
        scope = _scope(workspace_id)
        if scope is None:
            return {"ok": False, "backend": "memory-scope", "error": "workspace_id is required"}
        if isinstance(self.provider, DisabledLongTermMemoryProvider) and not isinstance(
            self.provider, UnavailableLongTermMemoryProvider
        ):
            return {"ok": True, "backend": "local-fallback", "deleted": 0}
        if self.session is None:
            return {"ok": False, "backend": "memory-mapping", "error": "database session is required"}
        mappings = self.session.exec(
            select(ExternalMemoryMapping).where(
                ExternalMemoryMapping.workspace_id == scope.workspace_id,
                ExternalMemoryMapping.provider == self.provider.name,
                ExternalMemoryMapping.sync_status != "deleted",
            )
        ).all()
        targets = list(dict.fromkeys((mapping.resource_type, mapping.local_id) for mapping in mappings))
        results = [
            await self.forget(
                workspace_id=scope.workspace_id,
                local_resource_type=resource_type,
                local_resource_id=local_id,
            )
            for resource_type, local_id in targets
        ]
        ok = all(result.get("ok") for result in results)
        return {
            "ok": ok,
            "backend": f"{self.provider.name}.delete-workspace",
            "deleted": sum(int(result.get("deleted") or 0) for result in results),
            "results": results,
            "error": None if ok else "one or more workspace memory records could not be deleted",
        }

    def _get_or_create_mapping(
        self,
        scope: MemoryScope,
        owner_user_id: UUID | None,
        resource_type: str,
        local_id: str,
        doc_hash: str,
        dataset_name: str,
    ) -> ExternalMemoryMapping:
        assert self.session is not None
        mapping = self.session.exec(
            select(ExternalMemoryMapping).where(
                ExternalMemoryMapping.workspace_id == scope.workspace_id,
                ExternalMemoryMapping.provider == self.provider.name,
                ExternalMemoryMapping.resource_type == resource_type,
                ExternalMemoryMapping.local_id == local_id,
                ExternalMemoryMapping.doc_hash == doc_hash,
            )
        ).first()
        if mapping is not None:
            mapping.dataset_name = dataset_name
            return mapping
        return ExternalMemoryMapping(
            workspace_id=scope.workspace_id,
            owner_user_id=owner_user_id,
            provider=self.provider.name,
            resource_type=resource_type,
            local_id=local_id,
            doc_hash=doc_hash,
            dataset_name=dataset_name,
        )

    def _apply_provider_result(self, mapping: ExternalMemoryMapping, result: ProviderResult) -> None:
        assert self.session is not None
        mapping.external_user_id = result.user_id
        mapping.external_dataset_id = result.dataset_id
        mapping.external_data_id = result.data_ids[0] if result.data_ids else None
        mapping.external_metadata = {
            "data_ids": result.data_ids,
            "backend": result.backend,
        }
        mapping.sync_status = "synced" if result.ok else "failed"
        mapping.last_error = None if result.ok else result.error
        mapping.synced_at = utcnow() if result.ok else None
        mapping.deleted_at = None
        mapping.updated_at = utcnow()
        self.session.add(mapping)
        self.session.commit()

    def _find_mappings(
        self,
        scope: MemoryScope,
        doc_hash: str | None,
        entity_urn: str | None,
        local_resource_type: str | None,
        local_resource_id: str | None,
    ) -> list[ExternalMemoryMapping]:
        assert self.session is not None
        stmt = select(ExternalMemoryMapping).where(
            ExternalMemoryMapping.workspace_id == scope.workspace_id,
            ExternalMemoryMapping.provider == self.provider.name,
            ExternalMemoryMapping.sync_status != "deleted",
        )
        if local_resource_type and local_resource_id:
            stmt = stmt.where(
                ExternalMemoryMapping.resource_type == local_resource_type,
                ExternalMemoryMapping.local_id == local_resource_id,
            )
        elif doc_hash:
            stmt = stmt.where(ExternalMemoryMapping.doc_hash == doc_hash)
        elif entity_urn:
            stmt = stmt.where(ExternalMemoryMapping.local_id == entity_urn)
        else:
            return []
        return list(self.session.exec(stmt.order_by(col(ExternalMemoryMapping.created_at).asc())).all())

    def _recall_dataset_names(self, scope: MemoryScope, filters: dict[str, Any]) -> list[str]:
        if filters.get("folder_path"):
            return [_dataset_name(filters)]
        if self.session is None or self.provider.name in {"disabled", "unavailable"}:
            return [_dataset_name(filters)]
        rows = self.session.exec(
            select(ExternalMemoryMapping).where(
                ExternalMemoryMapping.workspace_id == scope.workspace_id,
                ExternalMemoryMapping.provider == self.provider.name,
                ExternalMemoryMapping.sync_status == "synced",
            )
        ).all()
        names = list(dict.fromkeys(row.dataset_name for row in rows if row.dataset_name))
        return names or [_dataset_name(filters)]

    @asynccontextmanager
    async def _cognee_runtime(
        self,
        workspace_id: UUID | str,
        owner_user_id: UUID | str | None = None,
    ):
        if not isinstance(self.provider, CogneeLongTermMemoryProvider):
            yield
            return

        async with _COGNEE_RUNTIME_LOCK:
            previous = {key: os.environ.get(key) for key in COGNEE_RUNTIME_ENV_KEYS}
            _prepare_cognee_runtime(workspace_id, owner_user_id or self.owner_user_id)
            try:
                yield
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


def _load_provider(cognee_enabled: bool) -> LongTermMemoryProvider:
    if not cognee_enabled:
        return DisabledLongTermMemoryProvider()
    _prepare_cognee_environment()
    try:
        module = import_module("cognee")
    except Exception as exc:
        return UnavailableLongTermMemoryProvider(str(exc)[:2000] or exc.__class__.__name__)
    return CogneeLongTermMemoryProvider(module, _dataset_base())


def _prepare_cognee_runtime(workspace_id: UUID | str | None, owner_user_id: UUID | str | None = None) -> None:
    workspace = _uuid_or_none(workspace_id)
    owner = _uuid_or_none(owner_user_id)
    if workspace is None:
        return
    config = LLMFactory.get_config(workspace_id=workspace, owner_user_id=owner)
    if config.provider == "ollama":
        os.environ.pop("LLM_API_KEY", None)
        os.environ["LLM_PROVIDER"] = "ollama"
        os.environ["LLM_MODEL"] = config.model_name
        os.environ["LLM_ENDPOINT"] = config.effective_endpoint
        os.environ["OPENAI_API_BASE"] = config.effective_endpoint
        os.environ["OPENAI_BASE_URL"] = config.effective_endpoint
        return
    if not config.api_key:
        raise RuntimeError(f"{config.provider} API key is required for Cognee memory")
    os.environ["LLM_API_KEY"] = config.api_key
    os.environ["LLM_PROVIDER"] = COGNEE_PROVIDER_ALIASES.get(config.provider, config.provider)
    os.environ["LLM_MODEL"] = config.model_name
    os.environ["LLM_ENDPOINT"] = config.effective_endpoint
    os.environ["OPENAI_API_BASE"] = config.effective_endpoint
    os.environ["OPENAI_BASE_URL"] = config.effective_endpoint


def _prepare_cognee_environment(environ: dict[str, str] | None = None) -> None:
    env = environ if environ is not None else os.environ
    env.pop("LLM_API_KEY", None)
    env.pop("api_key", None)
    for key, value in COGNEE_LLM_DEFAULTS.items():
        if not env.get(key):
            env[key] = value
    storage_dir = env.get("STORAGE_DIR")
    if storage_dir:
        for key, dirname in COGNEE_CACHE_ENV_DEFAULTS.items():
            if not env.get(key):
                env[key] = os.path.join(storage_dir, dirname)
    env["ENABLE_BACKEND_ACCESS_CONTROL"] = "true"


def _dataset_name(metadata: dict[str, Any]) -> str:
    workspace_id = _uuid_or_none(metadata.get("workspace_id"))
    if workspace_id is None:
        raise ValueError("workspace_id is required for Cognee dataset isolation")
    folder = str(metadata.get("folder_path") or "").strip().strip("/")
    folder_part = _safe_dataset_part(folder)
    parts = [_dataset_base(), f"ws_{str(workspace_id).replace('-', '_')}"]
    if folder_part:
        parts.append(folder_part)
    return "_".join(parts)[:120]


def _dataset_base() -> str:
    return _safe_dataset_part(os.getenv("COGNEE_DATASET_NAME", COGNEE_DATASET_NAME)) or COGNEE_DATASET_NAME


def _safe_dataset_part(value: object) -> str:
    text = str(value or "").strip().strip("/")
    if not text:
        return ""
    return "".join(char if char.isalnum() else "_" for char in text.lower())[:64].strip("_")


def _scope(value: object) -> MemoryScope | None:
    workspace_id = _uuid_or_none(value)
    return MemoryScope(workspace_id=workspace_id) if workspace_id else None


def _uuid_or_none(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError, AttributeError):
        return None


def _failure(backend: str, doc_hash: str, error: str) -> dict[str, Any]:
    return {"ok": False, "backend": backend, "doc_hash": doc_hash, "sync_status": "failed", "error": error}


def _result_dict(result: ProviderResult, doc_hash: str, sync_status: str) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "backend": result.backend,
        "doc_hash": doc_hash,
        "dataset_name": result.dataset_name,
        "external_user_id": result.user_id,
        "external_dataset_id": result.dataset_id,
        "external_data_id": result.data_ids[0] if result.data_ids else None,
        "sync_status": sync_status,
        "error": result.error,
    }
