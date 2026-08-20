from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class WikiNodeRead(BaseModel):
    id: UUID
    title: str
    type: str
    yaml_meta: dict[str, Any]
    content_md: str | None
    raw_content_ref: str | None
    cognee_doc_hash: str | None
    created_at: datetime
    updated_at: datetime
    related_nodes: list[dict[str, Any]] = Field(default_factory=list)


class WikiNodeListItem(BaseModel):
    id: UUID
    title: str
    type: str
    aliases: list[str] = Field(default_factory=list)
    analysis_status: str | None = None
    credibility_score: float | None = None
    cognee_doc_hash: str | None = None
    updated_at: datetime


class WikiWebEnrichRequest(BaseModel):
    query: str | None = Field(default=None, max_length=300)
    top_k: int = Field(default=5, ge=1, le=10)


class UserRegister(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=200)
    workspace_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)
    remember: bool = True


class UserRead(BaseModel):
    id: UUID
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    created_at: datetime
    last_login_at: datetime | None = None


class WorkspaceRead(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    role: str
    active: bool = False
    created_at: datetime


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None


class AuthSessionRead(BaseModel):
    user: UserRead
    workspaces: list[WorkspaceRead] = Field(default_factory=list)
    active_workspace: WorkspaceRead | None = None


class AccountProfileRead(BaseModel):
    user: UserRead
    smtp_configured: bool


class ChangeEmailRequest(BaseModel):
    new_email: str = Field(min_length=3, max_length=255)
    current_password: str = Field(min_length=1, max_length=200)


class ChangeEmailConfirm(BaseModel):
    new_email: str = Field(min_length=3, max_length=255)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)
    confirm_password: str = Field(min_length=8, max_length=200)

    @model_validator(mode="after")
    def passwords_match(self) -> "ChangePasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("new passwords do not match")
        if self.current_password == self.new_password:
            raise ValueError("new password must differ from current password")
        return self


class ParseTaskRead(BaseModel):
    id: UUID
    filename: str
    status: str
    progress: int
    message: str | None
    wiki_node_id: UUID | None
    raw_content_ref: str | None
    created_at: datetime
    updated_at: datetime


class RawContentRead(BaseModel):
    node_id: UUID
    filename: str
    kind: str
    mime_type: str
    text: str | None = None
    base64: str | None = None


class SourceDocumentRead(BaseModel):
    id: UUID
    filename: str
    mime_type: str | None
    storage_backend: str
    storage_uri: str
    sha256: str
    size_bytes: int
    status: str
    document_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SourceSpanRead(BaseModel):
    id: UUID
    source_document_id: UUID
    parsed_artifact_id: UUID | None
    span_type: str
    locator: dict[str, Any]
    text: str
    char_start: int | None
    char_end: int | None
    confidence: float
    created_at: datetime


class EvidenceRead(BaseModel):
    id: UUID
    target_type: str
    target_id: str
    source_span_id: UUID
    quote: str | None
    relevance_score: float
    evidence_metadata: dict[str, Any]
    created_at: datetime
    span: SourceSpanRead


class DomainPackRead(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str | None
    owner_type: str
    version: str
    is_active: bool
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DomainPackCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    owner_type: str = Field(default="user", max_length=20)
    version: str = Field(default="1.0.0", min_length=1, max_length=30)
    is_active: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class DomainPackUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    is_active: bool | None = None
    config: dict[str, Any] | None = None


class DomainCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    owner_type: str = Field(default="user", max_length=20)
    domain_pack_ids: list[UUID] = Field(default_factory=list)


class DomainUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    is_active: bool | None = None
    domain_pack_ids: list[UUID] | None = None


class DomainRead(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str | None
    owner_type: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    domain_packs: list[DomainPackRead] = Field(default_factory=list)


class ClearKnowledgeRequest(BaseModel):
    delete_source_files: bool = False


class ClearKnowledgeResponse(BaseModel):
    ok: bool
    message: str
    deleted: dict[str, int]


class LLMConfigRead(BaseModel):
    id: int | None
    profile_name: str = "本地 Ollama"
    provider: str
    endpoint: str
    model_name: str
    has_api_key: bool = False
    api_key_masked: str | None = None
    temperature: float
    max_tokens: int
    is_active: bool
    updated_by: str | None
    updated_at: datetime | None = None


class LLMConfigUpdate(BaseModel):
    id: int | None = None
    profile_name: str = Field(default="未命名配置", min_length=1, max_length=80)
    provider: str = Field(min_length=2, max_length=30)
    endpoint: str = Field(min_length=1, max_length=255)
    model_name: str = Field(min_length=1, max_length=100)
    api_key: str | None = Field(default=None, max_length=4096)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=128, le=128000)
    is_active: bool = True
    updated_by: str | None = Field(default="user", max_length=50)

    @field_validator("provider")
    @classmethod
    def provider_allowed(cls, value: str) -> str:
        allowed = {"ollama", "openai", "minimax", "custom_api"}
        if value not in allowed:
            raise ValueError(f"provider must be one of {sorted(allowed)}")
        return value


class LLMConfigCreate(LLMConfigUpdate):
    pass


class WebSearchConfigRead(BaseModel):
    id: int | None
    profile_name: str = "MiniMax Web Search"
    provider: str
    endpoint: str
    has_api_key: bool = False
    api_key_masked: str | None = None
    command: str
    args: list[str] = Field(default_factory=list)
    tool_name: str
    timeout_seconds: int
    max_results: int
    is_active: bool
    updated_by: str | None
    updated_at: datetime | None = None


class WebSearchConfigUpdate(BaseModel):
    id: int | None = None
    profile_name: str = Field(default="MiniMax Web Search", min_length=1, max_length=80)
    provider: str = Field(default="minimax_mcp", min_length=2, max_length=30)
    endpoint: str = Field(default="https://api.minimaxi.com", min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    command: str = Field(default="uvx", min_length=1, max_length=120)
    args: list[str] = Field(default_factory=lambda: ["minimax-coding-plan-mcp", "-y"], max_length=20)
    tool_name: str = Field(default="web_search", min_length=1, max_length=80)
    timeout_seconds: int = Field(default=45, ge=5, le=180)
    max_results: int = Field(default=5, ge=1, le=10)
    is_active: bool = True
    updated_by: str | None = Field(default="user", max_length=50)

    @field_validator("provider")
    @classmethod
    def web_search_provider_allowed(cls, value: str) -> str:
        allowed = {"minimax_mcp"}
        if value not in allowed:
            raise ValueError(f"provider must be one of {sorted(allowed)}")
        return value


class WebSearchConfigCreate(WebSearchConfigUpdate):
    pass


class RecallRequest(BaseModel):
    query: str = Field(min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=5, ge=1, le=20)
    use_web_search: bool = False


class Citation(BaseModel):
    node_id: UUID | None = None
    title: str
    score: float
    link: str


class RecallResponse(BaseModel):
    answer: str
    citations: list[Citation]
    confidence: float
    memory_backend: str
    conversation_id: UUID | None = None


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=10)


class WebSearchResult(BaseModel):
    title: str
    url: str | None = None
    snippet: str
    raw: dict[str, Any] = Field(default_factory=dict)


class WebSearchResponse(BaseModel):
    query: str
    provider: str
    endpoint: str
    results: list[WebSearchResult]
    latency_ms: int
    raw: dict[str, Any] = Field(default_factory=dict)


class ForgetRequest(BaseModel):
    node_id: UUID | None = None
    doc_hash: str | None = None
    entity_urn: str | None = None
    reason: str | None = None
    delete_node: bool = False

    @model_validator(mode="after")
    def one_identifier_required(self) -> "ForgetRequest":
        if not self.node_id and not self.doc_hash and not self.entity_urn:
            raise ValueError("node_id, doc_hash or entity_urn is required")
        return self


class ImproveRequest(BaseModel):
    node_id: UUID | None = None
    doc_hash: str | None = None
    field: str = Field(min_length=1)
    correction: Any
    reason: str | None = None
    updated_by: str | None = "user"


class OperationResponse(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LLMTestResponse(BaseModel):
    ok: bool
    provider: str
    endpoint: str
    model_name: str
    latency_ms: int
    message: str


class WebSearchTestResponse(BaseModel):
    ok: bool
    provider: str
    endpoint: str
    latency_ms: int
    message: str
    results: list[WebSearchResult] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    ticker: str | None = None
    company_name: str | None = None
    company_short_name: str | None = None
    report_year: int | None = None
    folder_path: str | None = None
    status: str | None = None
    updated_at: datetime | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relation_type: str
    weight: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ScoringRequest(BaseModel):
    node_id: UUID
    weights: dict[str, float] = Field(default_factory=dict)


class ScoringResponse(BaseModel):
    node_id: UUID
    score: float
    grade: str
    rationale: list[str]
    config_snapshot: dict[str, Any]


class DialogRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    use_web_search: bool = False
    conversation_id: UUID | None = None


class ChatMessageRead(BaseModel):
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float | None = None
    memory_backend: str | None = None
    created_at: datetime


class ChatConversationRead(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ChatMessageRead] = Field(default_factory=list)


class ChatConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=180)
