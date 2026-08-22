from functools import lru_cache
from typing import Generator

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import get_settings
from app.core.llm_limits import DEFAULT_LLM_MAX_TOKENS
from app.core.secrets import encrypt_api_key, is_encrypted_secret
from app.models import Domain, DomainPack, DomainPackBinding, LLMConfigTable, WebSearchConfigTable


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_engine(settings.database_url, echo=False, connect_args=connect_args)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(get_engine())
    run_lightweight_migrations()


def run_lightweight_migrations() -> None:
    engine = get_engine()
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "llm_configs" not in table_names:
        return
    uuid_type = "UUID" if engine.dialect.name == "postgresql" else "CHAR(36)"
    scoped_tables = [
        "wiki_nodes",
        "source_documents",
        "parsed_artifacts",
        "source_spans",
        "evidence_links",
        "knowledge_edges",
        "domains",
        "domain_packs",
        "domain_pack_bindings",
        "llm_configs",
        "web_search_configs",
        "parse_tasks",
        "audit_logs",
        "external_memory_mappings",
        "email_change_codes",
    ]
    ddl: list[str] = []
    for table_name in scoped_tables:
        if table_name not in table_names:
            continue
        table_columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "workspace_id" not in table_columns:
            ddl.append(f"ALTER TABLE {table_name} ADD COLUMN workspace_id {uuid_type}")
        if "owner_user_id" not in table_columns:
            ddl.append(f"ALTER TABLE {table_name} ADD COLUMN owner_user_id {uuid_type}")
    columns = {column["name"] for column in inspector.get_columns("llm_configs")}
    if "profile_name" not in columns:
        ddl.append("ALTER TABLE llm_configs ADD COLUMN profile_name VARCHAR(80)")
    if "api_key" not in columns:
        ddl.append("ALTER TABLE llm_configs ADD COLUMN api_key TEXT")
    with engine.begin() as connection:
        for statement in ddl:
            connection.execute(text(statement))
        if "profile_name" not in columns:
            connection.execute(
                text("UPDATE llm_configs SET profile_name = :name WHERE profile_name IS NULL OR profile_name = ''"),
                {"name": "本地 Ollama"},
            )
    encrypt_stored_api_keys()


def encrypt_stored_api_keys() -> None:
    engine = get_engine()
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table_name in ("llm_configs", "web_search_configs"):
            if table_name not in table_names:
                continue
            table_columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "api_key" not in table_columns:
                continue
            rows = connection.execute(text(f"SELECT id, api_key FROM {table_name} WHERE api_key IS NOT NULL AND api_key <> ''")).mappings().all()
            for row in rows:
                stored = str(row["api_key"]).strip()
                if not stored or is_encrypted_secret(stored):
                    continue
                connection.execute(
                    text(f"UPDATE {table_name} SET api_key = :api_key WHERE id = :id"),
                    {"api_key": encrypt_api_key(stored), "id": row["id"]},
                )


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session


def seed_defaults() -> None:
    with Session(get_engine()) as session:
        existing = session.exec(select(LLMConfigTable).where(LLMConfigTable.is_active == True)).first()
        if existing is None:
            session.add(
                LLMConfigTable(
                    profile_name="本地 Ollama",
                    provider="ollama",
                    endpoint="http://localhost:11434",
                    model_name="qwen2.5:14b",
                    temperature=0.2,
                    max_tokens=DEFAULT_LLM_MAX_TOKENS,
                    is_active=True,
                    updated_by="system",
                )
            )
        _seed_llm_profiles(session)
        _seed_web_search_profiles(session)
        _seed_domain_packs(session)
        _seed_domains(session)
        session.commit()


def _seed_llm_profiles(session: Session) -> None:
    minimax = session.exec(select(LLMConfigTable).where(LLMConfigTable.provider == "minimax")).first()
    if minimax is None:
        session.add(
            LLMConfigTable(
                profile_name="MiniMax M3",
                provider="minimax",
                endpoint="https://api.minimaxi.com/v1",
                model_name="MiniMax-M3",
                temperature=0.2,
                max_tokens=DEFAULT_LLM_MAX_TOKENS,
                is_active=False,
                updated_by="system",
            )
        )


def _seed_web_search_profiles(session: Session) -> None:
    minimax = session.exec(select(WebSearchConfigTable).where(WebSearchConfigTable.provider == "minimax_mcp")).first()
    if minimax is None:
        session.add(
            WebSearchConfigTable(
                profile_name="MiniMax Token Plan Web Search",
                provider="minimax_mcp",
                endpoint="https://api.minimaxi.com",
                command="uvx",
                args=["minimax-coding-plan-mcp", "-y"],
                tool_name="web_search",
                timeout_seconds=45,
                max_results=5,
                is_active=True,
                updated_by="system",
            )
        )


def _seed_domain_packs(session: Session) -> None:
    for pack in _default_domain_packs():
        existing = session.exec(
            select(DomainPack).where(DomainPack.slug == pack["slug"], DomainPack.version == pack["version"])
        ).first()
        if existing is not None:
            continue
        session.add(DomainPack(**pack))


def _seed_domains(session: Session) -> None:
    for domain in _default_domains():
        existing = session.exec(select(Domain).where(Domain.slug == domain["slug"])).first()
        if existing is None:
            existing = Domain(
                slug=str(domain["slug"]),
                name=str(domain["name"]),
                description=str(domain["description"]),
                owner_type="system",
            )
            session.add(existing)
            session.flush()

        for pack_slug in domain["domain_pack_slugs"]:
            pack = session.exec(
                select(DomainPack).where(
                    DomainPack.slug == str(pack_slug),
                    DomainPack.is_active == True,
                )
            ).first()
            if pack is None:
                continue
            binding = session.exec(
                select(DomainPackBinding).where(
                    DomainPackBinding.domain_id == existing.id,
                    DomainPackBinding.domain_pack_id == pack.id,
                )
            ).first()
            if binding is None:
                session.add(DomainPackBinding(domain_id=existing.id, domain_pack_id=pack.id))


def _default_domains() -> list[dict[str, object]]:
    return [
        {
            "slug": "general-research",
            "name": "通用研究",
            "description": "适合普通文档、会议纪要、网页资料和跨领域知识整理。",
            "domain_pack_slugs": ["general-document"],
        },
        {
            "slug": "a-share-value-investing",
            "name": "A股价值投资",
            "description": "围绕A股年报解析、价值投资框架、风险合规和外部新闻进行研究。",
            "domain_pack_slugs": ["a-share-annual-report", "value-investing", "risk-compliance", "company-news"],
        },
        {
            "slug": "risk-and-compliance",
            "name": "风险合规研究",
            "description": "聚焦诉讼、处罚、监管、治理和经营风险事件。",
            "domain_pack_slugs": ["risk-compliance", "general-document"],
        },
    ]


def _default_domain_packs() -> list[dict[str, object]]:
    return [
        {
            "slug": "general-document",
            "name": "通用文档 Wiki 包",
            "description": "为任意上传文档提供基础摘要、实体、引用和 Wiki 页面结构。",
            "owner_type": "system",
            "version": "1.0.0",
            "config": {
                "routing": {"default": True, "extensions": ["pdf", "docx", "txt", "md"]},
                "node_types": ["general-doc", "source-document", "document-section"],
                "edge_types": ["cites", "mentions", "related-to"],
                "evidence": {"required_for": ["claims", "metrics", "relations"], "span_types": ["page", "paragraph", "table_row"]},
            },
        },
        {
            "slug": "a-share-annual-report",
            "name": "A股年报解析包",
            "description": "面向 A 股上市公司年报，抽取财务、业务、管理层讨论、风险和战略目标。",
            "owner_type": "system",
            "version": "1.0.0",
            "config": {
                "routing": {"keywords": ["年度报告", "年报", "董事会报告", "管理层讨论与分析"]},
                "node_types": [
                    "company-overview",
                    "company-finance-segment",
                    "company-strategy-goal",
                    "company-executive-profile",
                    "company-risk-operation",
                    "company-risk-legal",
                ],
                "edge_types": ["discloses", "has-segment", "has-risk", "sets-goal", "managed-by"],
                "evidence": {"required_for": ["financial_metrics", "risk_events", "strategy_goals"], "prefer": ["table_row", "page"]},
            },
        },
        {
            "slug": "value-investing",
            "name": "价值投资分析包",
            "description": "根据巴菲特价值投资理念抽取护城河、ROIC、现金流、资本配置和管理层可信度。",
            "owner_type": "system",
            "version": "1.0.0",
            "config": {
                "seed_concepts": ["护城河", "ROIC", "自由现金流", "资本配置", "安全边际", "管理层诚信"],
                "node_types": ["investment-concept", "valuation-framework", "investment-insight"],
                "edge_types": ["supports", "contradicts", "indicates", "requires-evidence"],
                "evidence": {"required_for": ["investment_insight", "valuation_claim"], "quote_required": True},
            },
        },
        {
            "slug": "risk-compliance",
            "name": "风险合规包",
            "description": "抽取诉讼、处罚、合规、经营和治理风险事件。",
            "owner_type": "system",
            "version": "1.0.0",
            "config": {
                "routing": {"keywords": ["诉讼", "处罚", "监管", "合规", "重大风险", "内控"]},
                "node_types": ["company-risk-legal", "company-risk-operation", "company-risk-compliance"],
                "edge_types": ["affects", "caused-by", "mitigated-by", "reported-in"],
                "evidence": {"required_for": ["risk_event"], "prefer": ["page", "paragraph"]},
            },
        },
        {
            "slug": "company-news",
            "name": "外部新闻包",
            "description": "抽取公告、新闻和市场事件，并记录来源权重和时间衰减信息。",
            "owner_type": "system",
            "version": "1.0.0",
            "config": {
                "node_types": ["company-news-official", "company-news-mainstream", "company-news-social"],
                "edge_types": ["reports", "corroborates", "updates", "conflicts-with"],
                "evidence": {"required_for": ["news_event"], "source_weight_required": True},
            },
        },
    ]
