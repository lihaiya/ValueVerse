from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.db.session import get_session
from app.models import EmailChangeCode, LLMConfigTable, UserAccount, WebSearchConfigTable, Workspace, WorkspaceMember, utcnow
from app.schemas import (
    AccountProfileRead,
    AuthSessionRead,
    ChangeEmailConfirm,
    ChangeEmailRequest,
    ChangePasswordRequest,
    LoginRequest,
    OperationResponse,
    UserRegister,
    UserRead,
    WorkspaceCreate,
    WorkspaceRead,
)
from app.services import mail as mail_service


SessionDep = Annotated[Session, Depends(get_session)]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class WorkspaceContext:
    user: UserAccount
    workspace: Workspace
    role: str

    @property
    def user_id(self) -> UUID:
        return self.user.id

    @property
    def workspace_id(self) -> UUID:
        return self.workspace.id


auth_router = APIRouter(prefix="/auth", tags=["auth"])
account_router = APIRouter(prefix="/account", tags=["account"])


@auth_router.post("/register", response_model=AuthSessionRead)
def register(payload: UserRegister, response: Response, session: SessionDep) -> AuthSessionRead:
    email = _normalize_email(payload.email)
    _validate_email(email)
    existing = session.exec(select(UserAccount).where(UserAccount.email == email)).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already registered")
    user = UserAccount(
        email=email,
        hashed_password=hash_password(payload.password),
        is_active=True,
        is_verified=False,
    )
    session.add(user)
    session.flush()
    workspace_name = (payload.workspace_name or "").strip() or "个人知识库"
    workspace = Workspace(name=workspace_name, owner_user_id=user.id)
    session.add(workspace)
    session.flush()
    session.add(WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="owner"))
    _seed_workspace_profiles(session, workspace.id, user.id, user.email)
    session.commit()
    session.refresh(user)
    session.refresh(workspace)
    _set_session_cookie(response, user.id)
    return _auth_session(session, user, active_workspace_id=workspace.id)


@auth_router.post("/login", response_model=AuthSessionRead)
def login(payload: LoginRequest, response: Response, session: SessionDep) -> AuthSessionRead:
    user = session.exec(select(UserAccount).where(UserAccount.email == _normalize_email(payload.email))).first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user is disabled")
    user.last_login_at = utcnow()
    session.add(user)
    session.commit()
    _set_session_cookie(response, user.id, remember=payload.remember)
    return _auth_session(session, user)


@auth_router.post("/logout", response_model=OperationResponse)
def logout(response: Response) -> OperationResponse:
    settings = get_settings()
    response.delete_cookie(
        settings.auth_cookie_name,
        httponly=True,
        secure=settings.effective_auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )
    return OperationResponse(ok=True, message="logged out")


@auth_router.get("/me", response_model=AuthSessionRead)
def me(context: Annotated[WorkspaceContext, Depends(get_workspace_context)], session: SessionDep) -> AuthSessionRead:
    return _auth_session(session, context.user, active_workspace_id=context.workspace_id)


@account_router.get("/profile", response_model=AccountProfileRead)
def account_profile(user: Annotated[UserAccount, Depends(get_current_user)]) -> AccountProfileRead:
    return AccountProfileRead(user=_user_read(user), smtp_configured=get_settings().smtp_configured)


@account_router.post("/email/request", response_model=OperationResponse)
def request_email_change(
    payload: ChangeEmailRequest,
    user: Annotated[UserAccount, Depends(get_current_user)],
    session: SessionDep,
) -> OperationResponse:
    new_email = _normalize_email(payload.new_email)
    _validate_email(new_email)
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="current password is incorrect")
    if new_email == user.email:
        raise HTTPException(status_code=422, detail="new email must differ from current email")
    existing = session.exec(select(UserAccount).where(UserAccount.email == new_email)).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already registered")
    if not get_settings().smtp_configured:
        raise HTTPException(status_code=503, detail="system email is not configured")

    for pending in session.exec(
        select(EmailChangeCode).where(
            EmailChangeCode.user_id == user.id,
            EmailChangeCode.consumed_at.is_(None),
        )
    ).all():
        pending.consumed_at = utcnow()
        session.add(pending)

    code = f"{secrets.randbelow(1_000_000):06d}"
    verification = EmailChangeCode(
        user_id=user.id,
        new_email=new_email,
        code_hash=_hash_email_change_code(user.id, new_email, code),
        expires_at=utcnow() + timedelta(seconds=get_settings().email_change_code_ttl_seconds),
    )
    session.add(verification)
    try:
        mail_service.send_email_change_code(new_email, code)
        session.commit()
    except mail_service.MailConfigurationError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=502, detail="failed to send verification email") from exc
    return OperationResponse(
        ok=True,
        message="verification code sent",
        details={"expires_in": get_settings().email_change_code_ttl_seconds},
    )


@account_router.post("/email/confirm", response_model=AuthSessionRead)
def confirm_email_change(
    payload: ChangeEmailConfirm,
    context: Annotated[WorkspaceContext, Depends(get_workspace_context)],
    session: SessionDep,
) -> AuthSessionRead:
    new_email = _normalize_email(payload.new_email)
    _validate_email(new_email)
    now = utcnow()
    verification = session.exec(
        select(EmailChangeCode)
        .where(
            EmailChangeCode.user_id == context.user.id,
            EmailChangeCode.new_email == new_email,
            EmailChangeCode.consumed_at.is_(None),
            EmailChangeCode.expires_at > now,
        )
        .order_by(EmailChangeCode.created_at.desc())
    ).first()
    if verification is None:
        raise HTTPException(status_code=400, detail="verification code expired or not found")
    if verification.attempt_count >= get_settings().email_change_code_max_attempts:
        raise HTTPException(status_code=429, detail="too many verification attempts")
    if not hmac.compare_digest(
        verification.code_hash,
        _hash_email_change_code(context.user.id, new_email, payload.code),
    ):
        verification.attempt_count += 1
        session.add(verification)
        session.commit()
        raise HTTPException(status_code=400, detail="invalid verification code")

    existing = session.exec(select(UserAccount).where(UserAccount.email == new_email)).first()
    if existing is not None and existing.id != context.user.id:
        raise HTTPException(status_code=409, detail="email already registered")
    context.user.email = new_email
    context.user.updated_at = now
    verification.consumed_at = now
    session.add(context.user)
    session.add(verification)
    session.commit()
    session.refresh(context.user)
    return _auth_session(session, context.user, active_workspace_id=context.workspace_id)


@account_router.post("/password", response_model=AuthSessionRead)
def change_password(
    payload: ChangePasswordRequest,
    context: Annotated[WorkspaceContext, Depends(get_workspace_context)],
    session: SessionDep,
) -> AuthSessionRead:
    if not verify_password(payload.current_password, context.user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="current password is incorrect")
    context.user.hashed_password = hash_password(payload.new_password)
    context.user.updated_at = utcnow()
    session.add(context.user)
    session.commit()
    session.refresh(context.user)
    return _auth_session(session, context.user, active_workspace_id=context.workspace_id)


@auth_router.get("/workspaces", response_model=list[WorkspaceRead])
def list_workspaces(context: Annotated[WorkspaceContext, Depends(get_workspace_context)], session: SessionDep) -> list[WorkspaceRead]:
    return _workspace_reads(session, context.user.id, active_workspace_id=context.workspace_id)


@auth_router.post("/workspaces", response_model=WorkspaceRead)
def create_workspace(
    payload: WorkspaceCreate,
    context: Annotated[WorkspaceContext, Depends(get_workspace_context)],
    session: SessionDep,
) -> WorkspaceRead:
    workspace = Workspace(
        name=payload.name.strip(),
        description=(payload.description or "").strip() or None,
        owner_user_id=context.user_id,
    )
    session.add(workspace)
    session.flush()
    session.add(WorkspaceMember(user_id=context.user_id, workspace_id=workspace.id, role="owner"))
    _seed_workspace_profiles(session, workspace.id, context.user_id, context.user.email)
    session.commit()
    session.refresh(workspace)
    return _workspace_read(workspace, "owner", active=True)


def get_current_user(
    session: SessionDep,
    session_token: Annotated[str | None, Cookie(alias=get_settings().auth_cookie_name)] = None,
) -> UserAccount:
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    user_id = _verify_session_token(session_token)
    user = session.get(UserAccount, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    return user


def get_workspace_context(
    session: SessionDep,
    user: Annotated[UserAccount, Depends(get_current_user)],
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> WorkspaceContext:
    membership_stmt = (
        select(WorkspaceMember, Workspace)
        .join(Workspace, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id, Workspace.is_active == True)
        .order_by(col(WorkspaceMember.created_at).asc())
    )
    memberships = session.exec(membership_stmt).all()
    if not memberships:
        workspace = Workspace(name="个人知识库", owner_user_id=user.id)
        session.add(workspace)
        session.flush()
        member = WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="owner")
        session.add(member)
        session.commit()
        session.refresh(workspace)
        return WorkspaceContext(user=user, workspace=workspace, role="owner")

    requested_id: UUID | None = None
    if x_workspace_id:
        try:
            requested_id = UUID(x_workspace_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid workspace id") from exc
    for member, workspace in memberships:
        if requested_id is None or workspace.id == requested_id:
            return WorkspaceContext(user=user, workspace=workspace, role=member.role)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace access denied")


WorkspaceContextDep = Annotated[WorkspaceContext, Depends(get_workspace_context)]


def require_owner_or_admin(context: WorkspaceContextDep) -> WorkspaceContext:
    if context.role not in {"owner", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace admin permission required")
    return context


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="password must be at least 8 characters")
    salt = os.urandom(16)
    iterations = 390000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, raw_iterations, raw_salt, raw_digest = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = _unb64(raw_salt)
        expected = _unb64(raw_digest)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _set_session_cookie(response: Response, user_id: UUID, remember: bool = True) -> None:
    settings = get_settings()
    max_age = settings.auth_session_max_age_seconds if remember else 60 * 60 * 12
    response.set_cookie(
        settings.auth_cookie_name,
        _create_session_token(user_id, max_age),
        max_age=max_age,
        httponly=True,
        secure=settings.effective_auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def _create_session_token(user_id: UUID, max_age_seconds: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=max_age_seconds)).timestamp()),
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signature = _sign(body)
    return f"{body}.{signature}"


def _verify_session_token(token: str) -> UUID:
    try:
        body, signature = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session") from exc
    if not hmac.compare_digest(signature, _sign(body)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    try:
        payload = json.loads(_unb64(body).decode("utf-8"))
        expires_at = int(payload.get("exp") or 0)
        user_id = UUID(str(payload.get("sub") or ""))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session") from exc
    if expires_at <= int(datetime.now(timezone.utc).timestamp()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")
    return user_id


def _sign(body: str) -> str:
    secret = get_settings().auth_secret.encode("utf-8")
    return _b64(hmac.new(secret, body.encode("utf-8"), hashlib.sha256).digest())


def _auth_session(session: Session, user: UserAccount, active_workspace_id: UUID | None = None) -> AuthSessionRead:
    workspaces = _workspace_reads(session, user.id, active_workspace_id=active_workspace_id)
    active = next((item for item in workspaces if item.active), workspaces[0] if workspaces else None)
    return AuthSessionRead(user=_user_read(user), workspaces=workspaces, active_workspace=active)


def _user_read(user: UserAccount) -> UserRead:
    return UserRead(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _workspace_reads(session: Session, user_id: UUID, active_workspace_id: UUID | None = None) -> list[WorkspaceRead]:
    rows = session.exec(
        select(WorkspaceMember, Workspace)
        .join(Workspace, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id, Workspace.is_active == True)
        .order_by(col(WorkspaceMember.created_at).asc())
    ).all()
    active_id = active_workspace_id or (rows[0][1].id if rows else None)
    return [_workspace_read(workspace, member.role, active=workspace.id == active_id) for member, workspace in rows]


def _workspace_read(workspace: Workspace, role: str, active: bool = False) -> WorkspaceRead:
    return WorkspaceRead(
        id=workspace.id,
        name=workspace.name,
        description=workspace.description,
        role=role,
        active=active,
        created_at=workspace.created_at,
    )


def _seed_workspace_profiles(session: Session, workspace_id: UUID, user_id: UUID, email: str) -> None:
    updated_by = email[:50]
    session.add(
        LLMConfigTable(
            workspace_id=workspace_id,
            owner_user_id=user_id,
            profile_name="本地 Ollama",
            provider="ollama",
            endpoint="http://localhost:11434",
            model_name="qwen3.6:27b",
            temperature=0.2,
            max_tokens=32768,
            is_active=True,
            updated_by=updated_by,
        )
    )
    session.add(
        LLMConfigTable(
            workspace_id=workspace_id,
            owner_user_id=user_id,
            profile_name="MiniMax M3",
            provider="minimax",
            endpoint="https://api.minimaxi.com/v1",
            model_name="MiniMax-M3",
            temperature=0.2,
            max_tokens=8192,
            is_active=False,
            updated_by=updated_by,
        )
    )
    session.add(
        WebSearchConfigTable(
            workspace_id=workspace_id,
            owner_user_id=user_id,
            profile_name="MiniMax Token Plan Web Search",
            provider="minimax_mcp",
            endpoint="https://api.minimaxi.com",
            command="uvx",
            args=["minimax-coding-plan-mcp", "-y"],
            tool_name="web_search",
            timeout_seconds=45,
            max_results=5,
            is_active=True,
            updated_by=updated_by,
        )
    )


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _validate_email(email: str) -> None:
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=422, detail="invalid email")


def _hash_email_change_code(user_id: UUID, new_email: str, code: str) -> str:
    material = f"email-change:{user_id}:{new_email}:{code}".encode("utf-8")
    return hmac.new(get_settings().auth_secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
