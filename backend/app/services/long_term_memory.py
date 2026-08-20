import hashlib
import inspect
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class MemoryScope:
    workspace_id: UUID


@dataclass(frozen=True)
class ExternalMemoryReference:
    dataset_name: str
    dataset_id: str
    data_id: str
    user_id: str | None = None


@dataclass
class ProviderResult:
    ok: bool
    backend: str
    dataset_name: str | None = None
    dataset_id: str | None = None
    data_ids: list[str] = field(default_factory=list)
    user_id: str | None = None
    items: list[Any] = field(default_factory=list)
    payload: Any = None
    error: str | None = None


class LongTermMemoryProvider(ABC):
    name: str

    @abstractmethod
    async def remember(
        self,
        content: str,
        dataset_name: str,
        scope: MemoryScope,
        metadata: dict[str, Any],
    ) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    async def recall(
        self,
        query: str,
        top_k: int,
        dataset_names: list[str],
        scope: MemoryScope,
    ) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    async def forget(
        self,
        reference: ExternalMemoryReference,
        scope: MemoryScope,
    ) -> ProviderResult:
        raise NotImplementedError


class CogneeLongTermMemoryProvider(LongTermMemoryProvider):
    name = "cognee"

    def __init__(self, module: Any, dataset_prefix: str) -> None:
        self.module = module
        self.dataset_prefix = dataset_prefix

    async def remember(
        self,
        content: str,
        dataset_name: str,
        scope: MemoryScope,
        metadata: dict[str, Any],
    ) -> ProviderResult:
        self._ensure_workspace_dataset(dataset_name, scope)
        dataset_id: str | None = None
        data_ids: list[str] = []
        user_id: str | None = None
        try:
            user = await self._workspace_user(scope)
            user_id = str(user.id)
            add_result = await _maybe_await(
                self.module.add(content, dataset_name=dataset_name, user=user, data_per_batch=1)
            )
            dataset_id = _object_id(add_result, "dataset_id")
            data_ids = _payload_data_ids(getattr(add_result, "payload", None))
            if dataset_id and not data_ids:
                data_ids = await _dataset_content_data_ids(dataset_id, content)
            if not dataset_id or not data_ids:
                raise RuntimeError("Cognee add completed without dataset_id/data_id")
            cognify_result = await _maybe_await(
                self.module.cognify(datasets=[UUID(dataset_id)], user=user, data_per_batch=1)
            )
            return ProviderResult(
                ok=True,
                backend="cognee.add+cognify",
                dataset_name=dataset_name,
                dataset_id=dataset_id,
                data_ids=data_ids,
                user_id=user_id,
                payload={"add": _safe_payload(add_result), "cognify": _safe_payload(cognify_result)},
            )
        except Exception as exc:
            return ProviderResult(
                ok=False,
                backend="cognee.add+cognify",
                dataset_name=dataset_name,
                dataset_id=dataset_id,
                data_ids=data_ids,
                user_id=user_id,
                error=_error_text(exc),
            )

    async def recall(
        self,
        query: str,
        top_k: int,
        dataset_names: list[str],
        scope: MemoryScope,
    ) -> ProviderResult:
        if not dataset_names:
            return ProviderResult(ok=True, backend="cognee.search", items=[])
        for dataset_name in dataset_names:
            self._ensure_workspace_dataset(dataset_name, scope)
        try:
            user = await self._workspace_user(scope)
            search_type = getattr(getattr(self.module, "SearchType", None), "CHUNKS", None)
            kwargs: dict[str, Any] = {
                "top_k": top_k,
                "datasets": dataset_names,
                "user": user,
            }
            if search_type is not None:
                kwargs["query_type"] = search_type
            payload = await _maybe_await(self.module.search(query, **kwargs))
            items = payload if isinstance(payload, list) else [payload]
            return ProviderResult(
                ok=True,
                backend="cognee.search",
                user_id=str(user.id),
                items=_safe_payload(items),
            )
        except Exception as exc:
            return ProviderResult(ok=False, backend="cognee.search", error=_error_text(exc))

    async def forget(
        self,
        reference: ExternalMemoryReference,
        scope: MemoryScope,
    ) -> ProviderResult:
        self._ensure_workspace_dataset(reference.dataset_name, scope)
        try:
            user = await self._workspace_user(scope)
            if reference.user_id and reference.user_id != str(user.id):
                raise PermissionError("external memory principal does not match workspace principal")
            payload = await _maybe_await(
                self.module.delete(
                    data_id=UUID(reference.data_id),
                    dataset_id=UUID(reference.dataset_id),
                    mode="hard",
                    user=user,
                )
            )
            return ProviderResult(
                ok=True,
                backend="cognee.delete-hard",
                dataset_name=reference.dataset_name,
                dataset_id=reference.dataset_id,
                data_ids=[reference.data_id],
                user_id=str(user.id),
                payload=_safe_payload(payload),
            )
        except Exception as exc:
            return ProviderResult(
                ok=False,
                backend="cognee.delete-hard",
                dataset_name=reference.dataset_name,
                dataset_id=reference.dataset_id,
                data_ids=[reference.data_id],
                user_id=reference.user_id,
                error=_error_text(exc),
            )

    async def _workspace_user(self, scope: MemoryScope) -> Any:
        setup = getattr(self.module, "setup", None)
        if setup is not None:
            await _maybe_await(setup())
        else:
            from cognee.modules.engine.operations.setup import setup as setup_cognee

            await setup_cognee()

        from cognee.modules.users.methods import create_user, get_user_by_email

        email = f"workspace-{scope.workspace_id}@memory.valueverse.example.com"
        user = await get_user_by_email(email)
        if user is not None:
            return user
        try:
            return await create_user(
                email=email,
                password=secrets.token_urlsafe(32),
                is_active=True,
                is_verified=True,
            )
        except Exception:
            user = await get_user_by_email(email)
            if user is None:
                raise
            return user

    def _ensure_workspace_dataset(self, dataset_name: str, scope: MemoryScope) -> None:
        expected = f"{self.dataset_prefix}_ws_{str(scope.workspace_id).replace('-', '_')}"
        if dataset_name != expected and not dataset_name.startswith(f"{expected}_"):
            raise PermissionError("dataset does not belong to the active workspace")


class DisabledLongTermMemoryProvider(LongTermMemoryProvider):
    name = "disabled"

    async def remember(self, content: str, dataset_name: str, scope: MemoryScope, metadata: dict[str, Any]) -> ProviderResult:
        return ProviderResult(ok=True, backend="local-fallback", dataset_name=dataset_name)

    async def recall(self, query: str, top_k: int, dataset_names: list[str], scope: MemoryScope) -> ProviderResult:
        return ProviderResult(ok=True, backend="local-fallback", items=[])

    async def forget(self, reference: ExternalMemoryReference, scope: MemoryScope) -> ProviderResult:
        return ProviderResult(ok=True, backend="local-fallback")


class UnavailableLongTermMemoryProvider(DisabledLongTermMemoryProvider):
    name = "unavailable"

    def __init__(self, error: str) -> None:
        self.error = error

    async def remember(self, content: str, dataset_name: str, scope: MemoryScope, metadata: dict[str, Any]) -> ProviderResult:
        return ProviderResult(ok=False, backend="cognee-unavailable", dataset_name=dataset_name, error=self.error)

    async def recall(self, query: str, top_k: int, dataset_names: list[str], scope: MemoryScope) -> ProviderResult:
        return ProviderResult(ok=False, backend="cognee-unavailable", items=[], error=self.error)

    async def forget(self, reference: ExternalMemoryReference, scope: MemoryScope) -> ProviderResult:
        return ProviderResult(ok=False, backend="cognee-unavailable", error=self.error)


def _object_id(value: Any, field_name: str) -> str | None:
    result = getattr(value, field_name, None)
    return str(result) if result else None


def _payload_data_ids(payload: Any) -> list[str]:
    values = payload if isinstance(payload, (list, tuple)) else [payload]
    result: list[str] = []
    for value in values:
        data_id = getattr(value, "id", None)
        if data_id:
            result.append(str(data_id))
        elif isinstance(value, dict) and value.get("id"):
            result.append(str(value["id"]))
    return list(dict.fromkeys(result))


async def _dataset_content_data_ids(dataset_id: str, content: str) -> list[str]:
    from cognee.modules.data.methods import get_dataset_data

    expected_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
    rows = await get_dataset_data(UUID(dataset_id))
    return [
        str(row.id)
        for row in rows
        if expected_hash in {str(row.content_hash or ""), str(row.raw_content_hash or "")}
    ]


def _safe_payload(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_payload(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:
            pass
    if hasattr(value, "to_json"):
        try:
            return value.to_json()
        except Exception:
            pass
    return str(value)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _error_text(error: Exception) -> str:
    return str(error).strip()[:2000] or error.__class__.__name__
