from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.session import create_db_and_tables, get_engine, seed_defaults
from app.main import create_app
from app.models import KnowledgeEdge, LLMConfigTable, ParseStatus, ParseTask, SourceDocument, WebSearchConfigTable, WikiNode
from app.api.routes import _normalize_ticker
from app.core.config import get_settings
from app.schemas import WebSearchResponse, WebSearchResult


client: TestClient | None = None
CURRENT_WORKSPACE_ID: str | None = None
CURRENT_USER_ID: str | None = None


@pytest.fixture(autouse=True)
def authenticated_workspace(tmp_path: Path) -> None:
    global CURRENT_WORKSPACE_ID, CURRENT_USER_ID, client
    settings = get_settings()
    original_database_url = settings.database_url
    original_cognee_enabled = settings.cognee_enabled
    original_llm_extraction_enabled = settings.llm_extraction_enabled
    original_max_upload_bytes = settings.max_upload_bytes
    original_app_env = settings.app_env
    original_auth_secret = settings.auth_secret
    original_api_docs_enabled = settings.api_docs_enabled
    original_api_key_encryption_secret = settings.api_key_encryption_secret
    settings.cognee_enabled = False
    settings.llm_extraction_enabled = False
    settings.database_url = f"sqlite:///{tmp_path / 'test_api.db'}"
    get_engine.cache_clear()
    client = TestClient(create_app())
    create_db_and_tables()
    seed_defaults()
    client.cookies.clear()
    email = f"pytest-{uuid4().hex}@example.com"
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "pytest-password", "workspace_name": "Pytest Workspace"},
    )
    assert response.status_code == 200
    body = response.json()
    CURRENT_USER_ID = body["user"]["id"]
    CURRENT_WORKSPACE_ID = body["active_workspace"]["id"]
    yield
    client.cookies.clear()
    client.close()
    client = None
    get_engine().dispose()
    get_engine.cache_clear()
    settings.database_url = original_database_url
    settings.cognee_enabled = original_cognee_enabled
    settings.llm_extraction_enabled = original_llm_extraction_enabled
    settings.max_upload_bytes = original_max_upload_bytes
    settings.app_env = original_app_env
    settings.auth_secret = original_auth_secret
    settings.api_docs_enabled = original_api_docs_enabled
    settings.api_key_encryption_secret = original_api_key_encryption_secret


def scope_kwargs() -> dict[str, UUID]:
    assert CURRENT_WORKSPACE_ID is not None
    assert CURRENT_USER_ID is not None
    return {"workspace_id": UUID(CURRENT_WORKSPACE_ID), "owner_user_id": UUID(CURRENT_USER_ID)}


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_account_profile_password_change_and_email_change(monkeypatch) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    original_smtp = {
        "smtp_host": settings.smtp_host,
        "smtp_username": settings.smtp_username,
        "smtp_password": settings.smtp_password,
        "smtp_from_email": settings.smtp_from_email,
    }
    settings.smtp_host = "smtp.example.com"
    settings.smtp_username = "wiki@example.com"
    settings.smtp_password = "smtp-secret"
    settings.smtp_from_email = "wiki@example.com"
    sent: dict[str, str] = {}

    def fake_send(recipient: str, code: str) -> None:
        sent["recipient"] = recipient
        sent["code"] = code

    monkeypatch.setattr("app.services.mail.send_email_change_code", fake_send)
    try:
        with client:
            profile_response = client.get("/api/account/profile")
            assert profile_response.status_code == 200
            assert profile_response.json()["smtp_configured"] is True

            password_response = client.post(
                "/api/account/password",
                json={
                    "current_password": "pytest-password",
                    "new_password": "pytest-password-2",
                    "confirm_password": "pytest-password-2",
                },
            )
            assert password_response.status_code == 200

            old_login = client.post(
                "/api/auth/login",
                json={"email": profile_response.json()["user"]["email"], "password": "pytest-password"},
            )
            assert old_login.status_code == 401
            new_login = client.post(
                "/api/auth/login",
                json={"email": profile_response.json()["user"]["email"], "password": "pytest-password-2"},
            )
            assert new_login.status_code == 200

            email_request = client.post(
                "/api/account/email/request",
                json={"new_email": "updated@example.com", "current_password": "pytest-password-2"},
            )
            assert email_request.status_code == 200
            assert sent == {"recipient": "updated@example.com", "code": sent["code"]}
            assert len(sent["code"]) == 6

            email_confirm = client.post(
                "/api/account/email/confirm",
                json={"new_email": "updated@example.com", "code": sent["code"]},
            )
            assert email_confirm.status_code == 200
            assert email_confirm.json()["user"]["email"] == "updated@example.com"
    finally:
        for name, value in original_smtp.items():
            setattr(settings, name, value)


def test_llm_config_roundtrip() -> None:
    with client:
        payload = {
            "profile_name": "pytest ollama",
            "provider": "ollama",
            "endpoint": "http://localhost:11434",
            "model_name": "qwen2.5:7b",
            "temperature": 0.3,
            "max_tokens": 2048,
            "updated_by": "pytest",
        }
        response = client.put("/api/settings/llm-config", json=payload)
        assert response.status_code == 200
        assert response.json()["model_name"] == "qwen2.5:7b"
        assert response.json()["profile_name"] == "pytest ollama"
        get_response = client.get("/api/settings/llm-config")
        assert get_response.status_code == 200
        assert get_response.json()["provider"] == "ollama"
        minimax_response = client.post(
            "/api/settings/llm-configs",
            json={
                "profile_name": "pytest minimax",
                "provider": "minimax",
                "endpoint": "https://api.minimaxi.com/v1",
                "model_name": "MiniMax-M3",
                "api_key": "test-secret-key",
                "temperature": 0.2,
                "max_tokens": 2048,
                "is_active": False,
                "updated_by": "pytest",
            },
        )
        assert minimax_response.status_code == 200
        minimax = minimax_response.json()
        assert minimax["has_api_key"] is True
        assert "test-secret-key" not in str(minimax)
        with Session(get_engine()) as session:
            stored = session.get(LLMConfigTable, minimax["id"])
            assert stored is not None
            assert stored.api_key is not None
            assert stored.api_key.startswith("enc:v1:")
            assert "test-secret-key" not in stored.api_key
        activate_response = client.post(f"/api/settings/llm-configs/{minimax['id']}/activate")
        assert activate_response.status_code == 200
        assert activate_response.json()["provider"] == "minimax"
        from app.services.llm_factory import LLMFactory

        runtime_config = LLMFactory.get_config(workspace_id=CURRENT_WORKSPACE_ID)
        assert runtime_config.api_key == "test-secret-key"


def test_web_search_config_roundtrip(monkeypatch) -> None:
    from app.services.web_search import WebSearchClient

    async def fake_search(self, query: str, top_k: int | None = None) -> WebSearchResponse:
        return WebSearchResponse(
            query=query,
            provider="minimax_mcp",
            endpoint="https://api.minimaxi.com",
            latency_ms=12,
            results=[WebSearchResult(title="MiniMax", url="https://example.com", snippet="ok")],
            raw={},
        )

    monkeypatch.setattr(WebSearchClient, "search", fake_search)
    suffix = uuid4().hex[:8]
    with client:
        response = client.post(
            "/api/settings/web-search-configs",
            json={
                "profile_name": f"pytest web {suffix}",
                "provider": "minimax_mcp",
                "endpoint": "https://api.minimaxi.com",
                "api_key": "test-token-plan-key",
                "command": "uvx",
                "args": ["minimax-coding-plan-mcp", "-y"],
                "tool_name": "web_search",
                "timeout_seconds": 30,
                "max_results": 3,
                "is_active": True,
                "updated_by": "pytest",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["has_api_key"] is True
        assert "test-token-plan-key" not in str(body)
        with Session(get_engine()) as session:
            stored = session.get(WebSearchConfigTable, body["id"])
            assert stored is not None
            assert stored.api_key is not None
            assert stored.api_key.startswith("enc:v1:")
            assert "test-token-plan-key" not in stored.api_key
        runtime_config = WebSearchClient.get_config(workspace_id=CURRENT_WORKSPACE_ID)
        assert runtime_config.api_key == "test-token-plan-key"
        test_response = client.post("/api/settings/test-web-search")
        assert test_response.status_code == 200
        assert test_response.json()["ok"] is True
        search_response = client.post("/api/web-search/search", json={"query": "MiniMax", "top_k": 1})
        assert search_response.status_code == 200
        assert search_response.json()["results"][0]["title"] == "MiniMax"


def test_existing_plaintext_api_keys_are_encrypted_on_migration() -> None:
    from app.db.session import encrypt_stored_api_keys

    assert CURRENT_WORKSPACE_ID is not None
    assert CURRENT_USER_ID is not None
    workspace_id = UUID(CURRENT_WORKSPACE_ID)
    owner_user_id = UUID(CURRENT_USER_ID)
    with Session(get_engine()) as session:
        llm_config = LLMConfigTable(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            profile_name="legacy llm",
            provider="minimax",
            endpoint="https://api.minimaxi.com/v1",
            model_name="MiniMax-M3",
            api_key="legacy-llm-secret",
            temperature=0.2,
            max_tokens=2048,
            is_active=False,
            updated_by="pytest",
        )
        web_search_config = WebSearchConfigTable(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            profile_name="legacy web",
            provider="minimax_mcp",
            endpoint="https://api.minimaxi.com",
            api_key="legacy-web-secret",
            command="uvx",
            args=["minimax-coding-plan-mcp", "-y"],
            tool_name="web_search",
            timeout_seconds=30,
            max_results=3,
            is_active=False,
            updated_by="pytest",
        )
        session.add(llm_config)
        session.add(web_search_config)
        session.commit()
        llm_id = llm_config.id
        web_id = web_search_config.id

    encrypt_stored_api_keys()

    with Session(get_engine()) as session:
        stored_llm = session.get(LLMConfigTable, llm_id)
        stored_web = session.get(WebSearchConfigTable, web_id)
        assert stored_llm is not None and stored_llm.api_key is not None
        assert stored_web is not None and stored_web.api_key is not None
        assert stored_llm.api_key.startswith("enc:v1:")
        assert stored_web.api_key.startswith("enc:v1:")
        assert "legacy-llm-secret" not in stored_llm.api_key
        assert "legacy-web-secret" not in stored_web.api_key


def test_wiki_web_enrich_updates_node_with_sources(monkeypatch) -> None:
    from app.services.llm_factory import LLMFactory
    from app.services.web_search import WebSearchClient

    async def fake_search(self, query: str, top_k: int | None = None) -> WebSearchResponse:
        return WebSearchResponse(
            query=query,
            provider="minimax_mcp",
            endpoint="https://api.minimaxi.com",
            latency_ms=10,
            results=[
                WebSearchResult(
                    title="义支付官方介绍",
                    url="https://example.com/yizhifu",
                    snippet="义支付面向市场经营户提供支付结算、交易服务和数字化经营能力。",
                )
            ],
            raw={},
        )

    async def fake_generate(prompt: str, response_format: str | None = None, **_: object) -> str:
        assert "义支付" in prompt
        return "# 义支付\n\n## 概念说明\n义支付面向市场经营户提供支付结算、交易服务和数字化经营能力。\n\n## 联网来源\n- [义支付官方介绍](https://example.com/yizhifu)"

    monkeypatch.setattr(WebSearchClient, "search", fake_search)
    monkeypatch.setattr(LLMFactory, "generate", staticmethod(fake_generate))
    suffix = uuid4().hex[:8]
    with client:
        with Session(get_engine()) as session:
            node = WikiNode(
                **scope_kwargs(),
                title=f"义支付 {suffix}",
                type="general-concept",
                yaml_meta={"analysis_status": "parsed", "description": "相关概念", "company_short_name": "小商品城"},
                content_md=f"# 义支付 {suffix}\n\n## 概念说明\n相关概念",
            )
            session.add(node)
            session.commit()
            node_id = str(node.id)
        response = client.post(f"/api/wiki/node/{node_id}/web-enrich", json={"top_k": 3})
        assert response.status_code == 200
        body = response.json()
        assert "支付结算" in body["content_md"]
        assert body["yaml_meta"]["web_enrichment"]["provider"] == "minimax_mcp"
        assert body["yaml_meta"]["external_sources"][0]["url"] == "https://example.com/yizhifu"


def test_wiki_web_enrich_falls_back_when_llm_fails(monkeypatch) -> None:
    from app.services.llm_factory import LLMFactory
    from app.services.web_search import WebSearchClient

    async def fake_search(self, query: str, top_k: int | None = None) -> WebSearchResponse:
        return WebSearchResponse(
            query=query,
            provider="minimax_mcp",
            endpoint="https://api.minimaxi.com",
            latency_ms=10,
            results=[WebSearchResult(title="公开资料", url=None, snippet="公开资料显示该概念仍需进一步核验。")],
            raw={},
        )

    async def fake_generate(prompt: str, response_format: str | None = None, **_: object) -> str:
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(WebSearchClient, "search", fake_search)
    monkeypatch.setattr(LLMFactory, "generate", staticmethod(fake_generate))
    suffix = uuid4().hex[:8]
    with client:
        with Session(get_engine()) as session:
            node = WikiNode(
                **scope_kwargs(),
                title=f"待补充概念 {suffix}",
                type="general-concept",
                yaml_meta={"analysis_status": "parsed", "description": "概念"},
                content_md=f"# 待补充概念 {suffix}\n\n## 概念说明\n概念",
            )
            session.add(node)
            session.commit()
            node_id = str(node.id)
        response = client.post(f"/api/wiki/node/{node_id}/web-enrich", json={"top_k": 1})
        assert response.status_code == 200
        body = response.json()
        assert "## 联网补充材料" in body["content_md"]
        assert body["yaml_meta"]["web_enrichment"]["result_count"] == 1


def test_wiki_web_enrich_query_uses_related_company_context(monkeypatch) -> None:
    from app.services.llm_factory import LLMFactory
    from app.services.web_search import WebSearchClient

    captured: dict[str, str] = {}

    async def fake_search(self, query: str, top_k: int | None = None) -> WebSearchResponse:
        captured["query"] = query
        return WebSearchResponse(
            query=query,
            provider="minimax_mcp",
            endpoint="https://api.minimaxi.com",
            latency_ms=10,
            results=[WebSearchResult(title="包华履历", url="https://example.com/baohua", snippet="包华为上市公司高管。")],
            raw={},
        )

    async def fake_generate(prompt: str, response_format: str | None = None, **_: object) -> str:
        return "# 包华\n\n## 人物概览\n待进一步核验。\n\n## 联网来源\n- 包华履历"

    monkeypatch.setattr(WebSearchClient, "search", fake_search)
    monkeypatch.setattr(LLMFactory, "generate", staticmethod(fake_generate))
    suffix = uuid4().hex[:8]
    with client:
        with Session(get_engine()) as session:
            person = WikiNode(
                **scope_kwargs(),
                title=f"包华 {suffix}",
                type="company-executive-profile",
                yaml_meta={"analysis_status": "parsed", "description": "副董事长、总经理"},
                content_md=f"# 包华 {suffix}\n\n## 人物概览\n副董事长、总经理",
            )
            company = WikiNode(
                **scope_kwargs(),
                title=f"小商品城 {suffix}",
                type="company-profile",
                yaml_meta={
                    "analysis_status": "parsed",
                    "company_short_name": f"小商品城 {suffix}",
                    "company_name": "浙江中国小商品城集团股份有限公司",
                    "ticker": "SH600415",
                },
                content_md="company",
            )
            session.add(person)
            session.add(company)
            session.commit()
            session.refresh(person)
            session.refresh(company)
            session.add(KnowledgeEdge(**scope_kwargs(), src_node_id=company.id, tgt_node_id=person.id, relation_type="HAS_CONCEPT"))
            session.commit()
            person_id = str(person.id)

        response = client.post(f"/api/wiki/node/{person_id}/web-enrich", json={"top_k": 1})
        assert response.status_code == 200
        assert "包华" in captured["query"]
        assert "小商品城" in captured["query"]
        assert "600415" in captured["query"]


def test_raw_content_falls_back_to_source_document_ref() -> None:
    suffix = uuid4().hex[:8]
    with client:
        with Session(get_engine()) as session:
            source = SourceDocument(
                **scope_kwargs(),
                filename=f"raw-source-{suffix}.txt",
                mime_type="text/plain",
                storage_uri=__file__,
                sha256=f"raw{suffix}".ljust(64, "0")[:64],
                size_bytes=12,
                status="parsed",
            )
            session.add(source)
            session.commit()
            session.refresh(source)
            node = WikiNode(
                **scope_kwargs(),
                title=f"来源 fallback {suffix}",
                type="general-concept",
                yaml_meta={"analysis_status": "parsed", "source_document_ids": [str(source.id)]},
                content_md="concept",
            )
            session.add(node)
            session.commit()
            node_id = str(node.id)

        response = client.get(f"/api/wiki/raw-content/{node_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "text"
        assert "test_raw_content_falls_back_to_source_document_ref" in body["text"]


def test_upload_text_document(tmp_path: Path) -> None:
    sample = b"title: sample\n\nAssets text"
    with client:
        response = client.post(
            "/api/docs/upload",
            data={"folder_path": "pytest/reports"},
            files={"file": ("sample.txt", sample, "text/plain")},
        )
        assert response.status_code == 200
        body = response.json()
        final_task = wait_for_task(body["id"])
        assert final_task["status"] == "completed"
        assert final_task["wiki_node_id"]
        node_response = client.get(f"/api/wiki/node/{final_task['wiki_node_id']}")
        assert node_response.status_code == 200
        source_document_id = node_response.json()["yaml_meta"]["source_document_id"]
        spans_response = client.get(f"/api/sources/document/{source_document_id}/spans")
        assert spans_response.status_code == 200
        assert spans_response.json()
        evidence_response = client.get(f"/api/wiki/node/{final_task['wiki_node_id']}/evidence")
        assert evidence_response.status_code == 200
        evidence = evidence_response.json()
        assert evidence
        assert evidence[0]["span"]["source_document_id"] == source_document_id
        assert final_task["raw_content_ref"].startswith("local://")
        sources_response = client.get("/api/sources/documents", params={"folder_path": "pytest/reports"})
        assert sources_response.status_code == 200
        assert any(item["id"] == source_document_id for item in sources_response.json())


def test_upload_rejects_files_over_configured_limit() -> None:
    settings = get_settings()
    original_max_upload_bytes = settings.max_upload_bytes
    settings.max_upload_bytes = 4
    try:
        with client:
            response = client.post(
                "/api/docs/upload",
                files={"file": ("too-large.txt", b"12345", "text/plain")},
            )
            assert response.status_code == 413
    finally:
        settings.max_upload_bytes = original_max_upload_bytes


def test_production_requires_non_default_auth_secret() -> None:
    settings = get_settings()
    original_app_env = settings.app_env
    original_auth_secret = settings.auth_secret
    try:
        settings.app_env = "production"
        settings.auth_secret = "dev-insecure-change-me"
        with pytest.raises(RuntimeError, match="AUTH_SECRET"):
            settings.validate_runtime_settings()
    finally:
        settings.app_env = original_app_env
        settings.auth_secret = original_auth_secret


def test_production_requires_non_default_api_key_encryption_secret() -> None:
    settings = get_settings()
    original_app_env = settings.app_env
    original_auth_secret = settings.auth_secret
    original_api_key_encryption_secret = settings.api_key_encryption_secret
    try:
        settings.app_env = "production"
        settings.auth_secret = "x" * 48
        settings.api_key_encryption_secret = "dev-insecure-api-key-encryption-secret"
        with pytest.raises(RuntimeError, match="API_KEY_ENCRYPTION_SECRET"):
            settings.validate_runtime_settings()
    finally:
        settings.app_env = original_app_env
        settings.auth_secret = original_auth_secret
        settings.api_key_encryption_secret = original_api_key_encryption_secret


def test_production_disables_api_docs_by_default() -> None:
    from app.main import create_app

    settings = get_settings()
    original_app_env = settings.app_env
    original_auth_secret = settings.auth_secret
    original_api_docs_enabled = settings.api_docs_enabled
    original_api_key_encryption_secret = settings.api_key_encryption_secret
    try:
        settings.app_env = "production"
        settings.auth_secret = "x" * 48
        settings.api_key_encryption_secret = "y" * 48
        settings.api_docs_enabled = None
        production_app = create_app()
        assert production_app.openapi_url is None
        assert production_app.docs_url is None
    finally:
        settings.app_env = original_app_env
        settings.auth_secret = original_auth_secret
        settings.api_docs_enabled = original_api_docs_enabled
        settings.api_key_encryption_secret = original_api_key_encryption_secret


def test_domain_packs_are_seeded() -> None:
    with client:
        response = client.get("/api/domain-packs")
        assert response.status_code == 200
        slugs = {item["slug"] for item in response.json()}
        assert {"general-document", "a-share-annual-report", "value-investing"}.issubset(slugs)


def test_domain_and_domain_pack_crud() -> None:
    suffix = uuid4().hex[:8]
    with client:
        pack_response = client.post(
            "/api/domain-packs",
            json={
                "slug": f"pytest-pack-{suffix}",
                "name": "Pytest Pack",
                "description": "created by tests",
                "version": "1.0.0",
                "config": {"node_types": ["test-node"], "edge_types": ["mentions"]},
            },
        )
        assert pack_response.status_code == 200
        pack = pack_response.json()

        domain_response = client.post(
            "/api/domains",
            json={
                "slug": f"pytest-domain-{suffix}",
                "name": "Pytest Domain",
                "description": "created by tests",
                "domain_pack_ids": [pack["id"]],
            },
        )
        assert domain_response.status_code == 200
        domain = domain_response.json()
        assert domain["domain_packs"][0]["id"] == pack["id"]

        update_response = client.put(
            f"/api/domain-packs/{pack['id']}",
            json={"name": "Pytest Pack Updated", "config": {"node_types": ["updated"]}},
        )
        assert update_response.status_code == 200
        assert update_response.json()["name"] == "Pytest Pack Updated"

        delete_domain = client.delete(f"/api/domains/{domain['id']}")
        assert delete_domain.status_code == 200
        delete_pack = client.delete(f"/api/domain-packs/{pack['id']}")
        assert delete_pack.status_code == 200


def test_clear_knowledge_keeps_configuration() -> None:
    with client:
        upload_response = client.post(
            "/api/docs/upload",
            files={"file": ("clear-me.txt", b"temporary knowledge", "text/plain")},
        )
        assert upload_response.status_code == 200
        final_task = wait_for_task(upload_response.json()["id"])
        assert final_task["wiki_node_id"]

        clear_response = client.post("/api/admin/clear-knowledge", json={"delete_source_files": False})
        assert clear_response.status_code == 200
        deleted = clear_response.json()["deleted"]
        assert deleted["wiki_nodes"] >= 1
        assert deleted["source_documents"] >= 1

        nodes_response = client.get("/api/wiki/nodes")
        assert nodes_response.status_code == 200
        assert nodes_response.json() == []

        sources_response = client.get("/api/sources/documents")
        assert sources_response.status_code == 200
        assert sources_response.json() == []

        packs_response = client.get("/api/domain-packs")
        assert packs_response.status_code == 200
        assert packs_response.json()


def test_recall_chinese_query_matches_metadata_and_content(monkeypatch) -> None:
    from app.services.llm_factory import LLMFactory
    from app.services.memory import MemoryClient

    calls: list[str] = []

    async def fake_generate(prompt: str, response_format: str | None = None, **_: object) -> str:
        calls.append(prompt)
        return "LLM 基于 [[用友网络2024年年报解析]] 生成的回答。"

    async def fake_recall(self, query: str, top_k: int, filters: dict | None = None) -> dict:
        return {"ok": True, "backend": "test-memory", "items": []}

    monkeypatch.setattr(LLMFactory, "generate", staticmethod(fake_generate))
    monkeypatch.setattr(MemoryClient, "recall", fake_recall)
    node = WikiNode(
        **scope_kwargs(),
        title="用友网络2024年年报解析",
        type="annual-report",
        yaml_meta={
            "analysis_status": "parsed",
            "company_short_name": "用友网络",
            "ticker": "600588",
            "report_year": 2024,
            "tags": ["财务", "风险"],
        },
        content_md="# 用友网络2024年年报解析\n\n营业收入、云服务收入和现金流是本年报的重点指标。",
    )
    with Session(get_engine()) as session:
        session.add(node)
        session.commit()

    with client:
        response = client.post("/api/memory/recall", json={"query": "用友网络2024年营收怎么样", "top_k": 3})
        assert response.status_code == 200
        body = response.json()
        assert body["citations"]
        assert any(citation["title"] == "用友网络2024年年报解析" for citation in body["citations"])
        assert "当前业务 Wiki 未检索到" not in body["answer"]
        assert "LLM 基于" in body["answer"]
        assert calls


def test_agent_dialog_includes_recent_conversation_in_next_llm_prompt(monkeypatch) -> None:
    from app.services.llm_factory import LLMFactory
    from app.services.memory import MemoryClient

    prompts: list[str] = []

    async def fake_generate(prompt: str, response_format: str | None = None, **_: object) -> str:
        prompts.append(prompt)
        return f"assistant answer {len(prompts)}"

    async def fake_recall(self, query: str, top_k: int, filters: dict | None = None) -> dict:
        return {"ok": True, "backend": "test-memory", "items": []}

    monkeypatch.setattr(LLMFactory, "generate", staticmethod(fake_generate))
    monkeypatch.setattr(MemoryClient, "recall", fake_recall)

    with client:
        first = client.post("/api/agent/dialog", json={"query": "first research question"})
        assert first.status_code == 200
        conversation_id = first.json()["conversation_id"]

        second = client.post(
            "/api/agent/dialog",
            json={"query": "continue that analysis", "conversation_id": conversation_id},
        )
        assert second.status_code == 200

    assert len(prompts) == 2
    assert "first research question" in prompts[1]
    assert "assistant answer 1" in prompts[1]
    assert "continue that analysis" in prompts[1]


def test_forget_delete_node_removes_local_wiki_records() -> None:
    suffix = uuid4().hex[:8]
    with Session(get_engine()) as session:
        report = WikiNode(
            **scope_kwargs(),
            title=f"删除测试报告 {suffix}",
            type="annual-report",
            yaml_meta={"analysis_status": "parsed"},
            content_md="report",
        )
        bad_node = WikiNode(
            **scope_kwargs(),
            title=f"包华及会 {suffix}",
            type="company-executive-profile",
            yaml_meta={"analysis_status": "parsed"},
            content_md="bad executive",
        )
        session.add(report)
        session.add(bad_node)
        session.commit()
        session.refresh(report)
        session.refresh(bad_node)
        session.add(KnowledgeEdge(**scope_kwargs(), src_node_id=report.id, tgt_node_id=bad_node.id, relation_type="HAS_EXECUTIVE"))
        session.commit()
        report_id = report.id
        bad_node_id = str(bad_node.id)

    with client:
        response = client.post(
            "/api/memory/forget",
            json={"node_id": bad_node_id, "delete_node": True, "reason": "bad extraction"},
        )
        assert response.status_code == 200
        details = response.json()["details"]
        assert details["deleted_local"]["wiki_nodes"] == 1
        assert details["deleted_local"]["knowledge_edges"] == 1
        assert client.get(f"/api/wiki/node/{bad_node_id}").status_code == 404
        search = client.get("/api/wiki/nodes", params={"q": f"包华及会 {suffix}"})
        assert search.status_code == 200
        assert search.json() == []

    with Session(get_engine()) as session:
        report = session.get(WikiNode, report_id)
        if report is not None:
            session.delete(report)
            session.commit()


def test_delete_source_document_removes_related_records() -> None:
    with client:
        upload_response = client.post(
            "/api/docs/upload",
            files={"file": ("delete-me.txt", b"title: delete me\n\nsingle document delete", "text/plain")},
        )
        assert upload_response.status_code == 200
        final_task = wait_for_task(upload_response.json()["id"])
        assert final_task["wiki_node_id"]
        node_response = client.get(f"/api/wiki/node/{final_task['wiki_node_id']}")
        source_document_id = node_response.json()["yaml_meta"]["source_document_id"]

        delete_response = client.delete(f"/api/sources/document/{source_document_id}")
        assert delete_response.status_code == 200
        deleted = delete_response.json()["details"]
        assert deleted["source_documents"] == 1
        assert deleted["wiki_nodes"] >= 1

        assert client.get(f"/api/wiki/node/{final_task['wiki_node_id']}").status_code == 404
        assert client.get(f"/api/sources/document/{source_document_id}/spans").status_code == 404


def test_processing_source_document_can_be_cancelled_and_delete_requested() -> None:
    suffix = uuid4().hex[:8]
    with Session(get_engine()) as session:
        cancel_task = ParseTask(**scope_kwargs(), filename=f"cancel-{suffix}.txt", status=ParseStatus.parsing, progress=45, raw_content_ref=f"local://cancel-{suffix}")
        session.add(cancel_task)
        session.flush()
        cancel_doc = SourceDocument(
            **scope_kwargs(),
            filename=f"cancel-{suffix}.txt",
            mime_type="text/plain",
            storage_uri=f"local://cancel-{suffix}",
            sha256=f"cancel{suffix}".ljust(64, "0")[:64],
            size_bytes=12,
            status="extracting",
            document_metadata={"parse_task_id": str(cancel_task.id)},
        )
        session.add(cancel_doc)

        delete_task = ParseTask(**scope_kwargs(), filename=f"delete-{suffix}.txt", status=ParseStatus.parsing, progress=45, raw_content_ref=f"local://delete-{suffix}")
        session.add(delete_task)
        session.flush()
        delete_doc = SourceDocument(
            **scope_kwargs(),
            filename=f"delete-{suffix}.txt",
            mime_type="text/plain",
            storage_uri=f"local://delete-{suffix}",
            sha256=f"delete{suffix}".ljust(64, "0")[:64],
            size_bytes=12,
            status="extracting",
            document_metadata={"parse_task_id": str(delete_task.id)},
        )
        session.add(delete_doc)
        session.commit()
        cancel_id = cancel_doc.id
        delete_id = delete_doc.id

    with client:
        cancel_response = client.post(f"/api/sources/document/{cancel_id}/cancel")
        assert cancel_response.status_code == 200
        assert cancel_response.json()["details"]["status"] == "cancel_requested"

        delete_response = client.delete(f"/api/sources/document/{delete_id}", params={"delete_source_file": True})
        assert delete_response.status_code == 200
        assert delete_response.json()["details"]["delete_pending"] is True


def test_upload_creates_company_concepts_and_graph_edges() -> None:
    suffix = uuid4().hex[:6]
    ticker = f"88{suffix[:4]}"
    sample = f"""---
title: 测试股份{suffix}2024年年报
type: annual-report
ticker: {ticker}
company_name: 测试股份{suffix}有限公司
company_short_name: 测试股份{suffix}
report_year: 2024
business_segments:
  - 云业务
risks:
  - 现金流风险
management_strategy:
  - 提质增效
investment_view:
  - 自由现金流
related:
  - ROIC
---
测试股份{suffix}年度材料，包含云业务、现金流风险、提质增效与自由现金流。
""".encode("utf-8")
    with client:
        upload_response = client.post(
            "/api/docs/upload",
            files={"file": (f"related-{suffix}.txt", sample, "text/plain")},
        )
        assert upload_response.status_code == 200
        final_task = wait_for_task(upload_response.json()["id"])
        assert final_task["status"] == "completed"
        node_response = client.get(f"/api/wiki/node/{final_task['wiki_node_id']}")
        assert node_response.status_code == 200
        related = node_response.json()["related_nodes"]
        assert any(item["type"] == "company-profile" for item in related)
        assert any(item["type"] == "company-finance-segment" for item in related)

        graph_response = client.get("/api/graph/nodes", params={"ticker": ticker})
        assert graph_response.status_code == 200
        graph = graph_response.json()
        assert any(node["type"] == "company-profile" for node in graph["nodes"])
        assert any(node["type"] == "company-finance-segment" for node in graph["nodes"])
        assert any(edge["relation_type"] == "ABOUT_COMPANY" for edge in graph["edges"])


def test_reparse_source_document_reuses_source_file() -> None:
    with client:
        upload_response = client.post(
            "/api/docs/upload",
            data={"folder_path": "pytest/reparse"},
            files={"file": ("reparse-me.txt", b"title: reparse me\n\nfirst pass", "text/plain")},
        )
        assert upload_response.status_code == 200
        first_task = wait_for_task(upload_response.json()["id"])
        node_response = client.get(f"/api/wiki/node/{first_task['wiki_node_id']}")
        source_document_id = node_response.json()["yaml_meta"]["source_document_id"]

        reparse_response = client.post(f"/api/sources/document/{source_document_id}/reparse")
        assert reparse_response.status_code == 200
        second_task = wait_for_task(reparse_response.json()["id"])
        assert second_task["status"] == "completed"
        assert second_task["wiki_node_id"]
        assert second_task["raw_content_ref"] == first_task["raw_content_ref"]

        sources_response = client.get("/api/sources/documents", params={"folder_path": "pytest/reparse"})
        assert sources_response.status_code == 200
        assert [item["id"] for item in sources_response.json()].count(source_document_id) == 1


def test_graph_infers_report_year_sequence() -> None:
    suffix = uuid4().hex[:8]
    ticker = f"PY{suffix[:4]}"
    with Session(get_engine()) as session:
        session.add(
            WikiNode(
                **scope_kwargs(),
                title=f"测试公司 {suffix} 2024 年报",
                type="annual-report",
                yaml_meta={"ticker": ticker, "report_year": 2024, "tags": ["财务"], "analysis_status": "parsed"},
                content_md="2024 report",
            )
        )
        session.add(
            WikiNode(
                **scope_kwargs(),
                title=f"测试公司 {suffix} 2025 年报",
                type="annual-report",
                yaml_meta={"ticker": ticker, "report_year": 2025, "tags": ["财务"], "analysis_status": "parsed"},
                content_md="2025 report",
            )
        )
        session.commit()

    with client:
        response = client.get("/api/graph/nodes", params={"ticker": ticker})
        assert response.status_code == 200
        body = response.json()
        assert len(body["nodes"]) == 2
        assert any(edge["relation_type"] == "REPORT_YEAR_SEQUENCE" for edge in body["edges"])


def test_normalize_ticker_for_graph_grouping() -> None:
    assert _normalize_ticker("SH600588") == "600588"
    assert _normalize_ticker("600588.SH") == "600588"
    assert _normalize_ticker("600588") == "600588"


def wait_for_task(task_id: str) -> dict[str, object]:
    for _ in range(20):
        response = client.get(f"/api/parse/status/{task_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "failed"}:
            return body
    raise AssertionError(f"task did not finish: {task_id}")
