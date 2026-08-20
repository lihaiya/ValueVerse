from dataclasses import dataclass
from pathlib import Path

from app.services.storage import get_object_storage


@dataclass
class RawContent:
    filename: str
    kind: str
    mime_type: str
    bytes_content: bytes
    text: str | None = None


def load_raw_content(path: Path | str) -> RawContent:
    path = get_object_storage().resolve(path)
    if not path.exists():
        raise FileNotFoundError(f"raw content not found: {path}")
    suffix = path.suffix.lower()
    data = path.read_bytes()
    if suffix == ".pdf":
        return RawContent(filename=path.name, kind="pdf", mime_type="application/pdf", bytes_content=data)
    text = _decode_text(data)
    mime = "text/markdown" if suffix == ".md" else "text/plain"
    return RawContent(filename=path.name, kind="text", mime_type=mime, bytes_content=data, text=text)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "gbk", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")
