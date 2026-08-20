import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


ENCRYPTED_SECRET_PREFIX = "enc:v1:"


def encrypt_api_key(value: str | None) -> str | None:
    api_key = (value or "").strip()
    if not api_key:
        return None
    if is_encrypted_secret(api_key):
        return api_key
    token = _fernet(_encryption_secret()).encrypt(api_key.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_SECRET_PREFIX}{token}"


def decrypt_api_key(value: str | None) -> str | None:
    stored = (value or "").strip()
    if not stored:
        return None
    if not is_encrypted_secret(stored):
        return stored
    token = stored.removeprefix(ENCRYPTED_SECRET_PREFIX).encode("ascii")
    try:
        return _fernet(_encryption_secret()).decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("could not decrypt stored API key; check API_KEY_ENCRYPTION_SECRET") from exc


def is_encrypted_secret(value: str | None) -> bool:
    return bool(value and value.strip().startswith(ENCRYPTED_SECRET_PREFIX))


@lru_cache(maxsize=8)
def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def _encryption_secret() -> str:
    return get_settings().api_key_encryption_secret.strip()
