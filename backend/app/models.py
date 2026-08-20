from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ParseStatus(str, Enum):
    pending = "pending"
    parsing = "parsing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class WikiNode(SQLModel, table=True):
    __tablename__ = "wiki_nodes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    title: str = Field(max_length=255, index=True)
    type: str = Field(max_length=30, index=True)
    yaml_meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    content_md: str | None = Field(default=None, sa_column=Column(Text))
    raw_content_ref: str | None = Field(default=None, sa_column=Column(Text))
    cognee_doc_hash: str | None = Field(default=None, max_length=64, index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class SourceDocument(SQLModel, table=True):
    __tablename__ = "source_documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    filename: str = Field(max_length=255, index=True)
    mime_type: str | None = Field(default=None, max_length=120)
    storage_backend: str = Field(default="local", max_length=30, index=True)
    storage_uri: str = Field(sa_column=Column(Text, nullable=False))
    sha256: str = Field(max_length=64, index=True)
    size_bytes: int = Field(sa_column=Column(Integer, nullable=False))
    status: str = Field(default="uploaded", max_length=30, index=True)
    document_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class ParsedArtifact(SQLModel, table=True):
    __tablename__ = "parsed_artifacts"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    source_document_id: UUID = Field(foreign_key="source_documents.id", index=True)
    parser_name: str = Field(max_length=80, index=True)
    artifact_type: str = Field(default="text", max_length=30, index=True)
    content_text: str | None = Field(default=None, sa_column=Column(Text))
    content_ref: str | None = Field(default=None, sa_column=Column(Text))
    quality: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class SourceSpan(SQLModel, table=True):
    __tablename__ = "source_spans"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    source_document_id: UUID = Field(foreign_key="source_documents.id", index=True)
    parsed_artifact_id: UUID | None = Field(default=None, foreign_key="parsed_artifacts.id", index=True)
    span_type: str = Field(max_length=30, index=True)
    locator: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    text: str = Field(sa_column=Column(Text, nullable=False))
    char_start: int | None = Field(default=None, sa_column=Column(Integer))
    char_end: int | None = Field(default=None, sa_column=Column(Integer))
    confidence: float = Field(default=1.0, sa_column=Column(Float))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class EvidenceLink(SQLModel, table=True):
    __tablename__ = "evidence_links"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    target_type: str = Field(max_length=50, index=True)
    target_id: str = Field(max_length=100, index=True)
    source_span_id: UUID = Field(foreign_key="source_spans.id", index=True)
    quote: str | None = Field(default=None, sa_column=Column(Text))
    relevance_score: float = Field(default=0.5, sa_column=Column(Float))
    evidence_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class KnowledgeEdge(SQLModel, table=True):
    __tablename__ = "knowledge_edges"
    __table_args__ = (
        UniqueConstraint("src_node_id", "tgt_node_id", "relation_type", name="uq_knowledge_edge"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    src_node_id: UUID = Field(foreign_key="wiki_nodes.id", index=True)
    tgt_node_id: UUID = Field(foreign_key="wiki_nodes.id", index=True)
    relation_type: str = Field(max_length=50, index=True)
    weight: float = Field(default=1.0, sa_column=Column(Float))
    edge_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", JSON, nullable=False))


class Domain(SQLModel, table=True):
    __tablename__ = "domains"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_domain_workspace_slug"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    slug: str = Field(max_length=80, index=True)
    name: str = Field(max_length=120)
    description: str | None = Field(default=None, sa_column=Column(Text))
    owner_type: str = Field(default="user", max_length=20, index=True)
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class DomainPack(SQLModel, table=True):
    __tablename__ = "domain_packs"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", "version", name="uq_domain_pack_workspace_slug_version"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    slug: str = Field(max_length=80, index=True)
    name: str = Field(max_length=120)
    description: str | None = Field(default=None, sa_column=Column(Text))
    owner_type: str = Field(default="system", max_length=20, index=True)
    version: str = Field(default="1.0.0", max_length=30)
    is_active: bool = Field(default=True, index=True)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class DomainPackBinding(SQLModel, table=True):
    __tablename__ = "domain_pack_bindings"
    __table_args__ = (UniqueConstraint("domain_id", "domain_pack_id", name="uq_domain_pack_binding"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    domain_id: UUID = Field(foreign_key="domains.id", index=True)
    domain_pack_id: UUID = Field(foreign_key="domain_packs.id", index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class LLMConfigTable(SQLModel, table=True):
    __tablename__ = "llm_configs"

    id: int | None = Field(default=None, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    profile_name: str = Field(default="本地 Ollama", max_length=80, index=True)
    provider: str = Field(max_length=30)
    endpoint: str = Field(max_length=255)
    model_name: str = Field(max_length=100)
    api_key: str | None = Field(default=None, sa_column=Column(Text))
    temperature: float = Field(default=0.2)
    max_tokens: int = Field(default=4096)
    is_active: bool = Field(default=True, index=True)
    updated_by: str | None = Field(default=None, max_length=50)
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class WebSearchConfigTable(SQLModel, table=True):
    __tablename__ = "web_search_configs"

    id: int | None = Field(default=None, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    profile_name: str = Field(default="MiniMax Web Search", max_length=80, index=True)
    provider: str = Field(max_length=30)
    endpoint: str = Field(default="https://api.minimaxi.com", max_length=255)
    api_key: str | None = Field(default=None, sa_column=Column(Text))
    command: str = Field(default="uvx", max_length=120)
    args: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    tool_name: str = Field(default="web_search", max_length=80)
    timeout_seconds: int = Field(default=45)
    max_results: int = Field(default=5)
    is_active: bool = Field(default=True, index=True)
    updated_by: str | None = Field(default=None, max_length=50)
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class ParseTask(SQLModel, table=True):
    __tablename__ = "parse_tasks"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    filename: str = Field(max_length=255)
    status: ParseStatus = Field(default=ParseStatus.pending, sa_column=Column(String(20), nullable=False))
    progress: int = Field(default=0)
    message: str | None = Field(default=None, sa_column=Column(Text))
    wiki_node_id: UUID | None = Field(default=None, foreign_key="wiki_nodes.id", index=True)
    raw_content_ref: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID | None = Field(default=None, foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    action: str = Field(max_length=50, index=True)
    target_type: str = Field(max_length=50, index=True)
    target_id: str = Field(max_length=100, index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_by: str | None = Field(default=None, max_length=50)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class UserAccount(SQLModel, table=True):
    __tablename__ = "user_accounts"
    __table_args__ = (UniqueConstraint("email", name="uq_user_account_email"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(max_length=255, index=True)
    hashed_password: str = Field(sa_column=Column(Text, nullable=False))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    is_superuser: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    is_verified: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    last_login_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class EmailChangeCode(SQLModel, table=True):
    __tablename__ = "email_change_codes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user_accounts.id", index=True)
    new_email: str = Field(max_length=255, index=True)
    code_hash: str = Field(max_length=128)
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    attempt_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    consumed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(max_length=120, index=True)
    description: str | None = Field(default=None, sa_column=Column(Text))
    owner_user_id: UUID = Field(foreign_key="user_accounts.id", index=True)
    is_active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class WorkspaceMember(SQLModel, table=True):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspaces.id", index=True)
    user_id: UUID = Field(foreign_key="user_accounts.id", index=True)
    role: str = Field(default="member", max_length=20, index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class ChatConversation(SQLModel, table=True):
    __tablename__ = "chat_conversations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspaces.id", index=True)
    owner_user_id: UUID = Field(foreign_key="user_accounts.id", index=True)
    title: str = Field(default="新对话", max_length=180, index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    conversation_id: UUID = Field(foreign_key="chat_conversations.id", index=True)
    workspace_id: UUID = Field(foreign_key="workspaces.id", index=True)
    owner_user_id: UUID = Field(foreign_key="user_accounts.id", index=True)
    role: str = Field(max_length=20, index=True)
    content: str = Field(sa_column=Column(Text, nullable=False))
    citations: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    confidence: float | None = Field(default=None, sa_column=Column(Float))
    memory_backend: str | None = Field(default=None, max_length=120)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class ExternalMemoryMapping(SQLModel, table=True):
    __tablename__ = "external_memory_mappings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "resource_type",
            "local_id",
            "doc_hash",
            name="uq_external_memory_local_resource",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspaces.id", index=True)
    owner_user_id: UUID | None = Field(default=None, foreign_key="user_accounts.id", index=True)
    provider: str = Field(max_length=40, index=True)
    resource_type: str = Field(max_length=50, index=True)
    local_id: str = Field(max_length=100, index=True)
    doc_hash: str = Field(max_length=64, index=True)
    dataset_name: str = Field(max_length=120, index=True)
    external_user_id: str | None = Field(default=None, max_length=100, index=True)
    external_dataset_id: str | None = Field(default=None, max_length=100, index=True)
    external_data_id: str | None = Field(default=None, max_length=100, index=True)
    sync_status: str = Field(default="pending", max_length=30, index=True)
    attempt_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    last_error: str | None = Field(default=None, sa_column=Column(Text))
    external_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    synced_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    deleted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
