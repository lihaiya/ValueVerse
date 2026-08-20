from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings


LOCAL_URI_PREFIX = "local://"


@dataclass(frozen=True)
class StoredObject:
    backend: str
    uri: str
    path: Path
    size_bytes: int


class LocalObjectStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().storage_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, *, data: bytes, filename: str, category: str = "raw") -> StoredObject:
        safe_name = _safe_filename(filename)
        relative_path = Path(category) / f"{uuid4()}-{safe_name}"
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(
            backend="local",
            uri=f"{LOCAL_URI_PREFIX}{relative_path.as_posix()}",
            path=path,
            size_bytes=len(data),
        )

    def resolve(self, uri_or_path: str | Path) -> Path:
        value = str(uri_or_path)
        if value.startswith(LOCAL_URI_PREFIX):
            relative = value[len(LOCAL_URI_PREFIX) :].lstrip("/")
            return self.root / Path(relative)
        return Path(value)

    def read(self, uri_or_path: str | Path) -> bytes:
        return self.resolve(uri_or_path).read_bytes()


def get_object_storage() -> LocalObjectStorage:
    return LocalObjectStorage()


def _safe_filename(filename: str) -> str:
    keep = [char if char.isalnum() or char in "._-" else "_" for char in Path(filename).name]
    cleaned = "".join(keep).strip("._")
    return cleaned or "upload"
