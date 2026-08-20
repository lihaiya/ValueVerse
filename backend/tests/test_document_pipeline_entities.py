from app.services.document_pipeline import (
    _append_uploaded_material_update,
    _extract_executives,
    _extract_related_concepts,
    _looks_like_person_name,
    _should_update_concept_content,
)
from app.models import SourceDocument
from app.services.recall import _clean_llm_answer


def test_related_concepts_keep_related_links_when_metrics_are_many() -> None:
    metadata = {
        "title": "小商品城2025年年报解析",
        "company_short_name": "小商品城",
        "company_name": "浙江中国小商品城集团股份有限公司",
        "related": ["小商品城", "全球数贸中心", "义支付"],
        "key_metrics": {f"指标{i}": i for i in range(80)},
    }

    concepts = _extract_related_concepts(metadata, "")
    titles = {item["title"] for item in concepts}

    assert "全球数贸中心" in titles
    assert "义支付" in titles
    assert "小商品城" not in titles


def test_related_concepts_include_markdown_wikilinks() -> None:
    metadata = {
        "title": "小商品城2025年年报解析",
        "company_short_name": "小商品城",
        "company_name": "浙江中国小商品城集团股份有限公司",
    }
    markdown = "# 小商品城2025年年报解析\n\n推进 [[全球数贸中心]] 与 [[义支付]] 建设。"

    concepts = _extract_related_concepts(metadata, markdown)
    titles = {item["title"] for item in concepts}

    assert {"全球数贸中心", "义支付"}.issubset(titles)


def test_related_concepts_normalize_stringified_single_item_lists() -> None:
    metadata = {
        "title": "小商品城2025年年报解析",
        "company_short_name": "小商品城",
        "company_name": "浙江中国小商品城集团股份有限公司",
        "related": ["['义支付']"],
    }

    concepts = _extract_related_concepts(metadata, "")
    titles = {item["title"] for item in concepts}

    assert "义支付" in titles
    assert "['义支付']" not in titles


def test_wikilink_matching_executive_uses_executive_type() -> None:
    metadata = {
        "title": "小商品城2025年年报解析",
        "company_short_name": "小商品城",
        "company_name": "浙江中国小商品城集团股份有限公司",
        "executives": [{"name": "王栋", "role": "董事长", "description": "董事长，从年报治理信息中抽取。"}],
    }

    concepts = _extract_related_concepts(metadata, "董事长 [[王栋]] 负责董事会工作。")
    by_title = {(item["title"], item["type"]) for item in concepts}

    assert ("王栋", "company-executive-profile") in by_title
    assert ("王栋", "general-concept") not in by_title


def test_person_name_filter_rejects_connected_table_noise() -> None:
    assert not _looks_like_person_name("包华及会")
    assert not _looks_like_person_name("吴政平及")
    assert not _looks_like_person_name("发展集团")
    assert _looks_like_person_name("王栋")


def test_extract_executives_from_annual_report_text() -> None:
    text = (
        "公司的法定代表人 王栋\n"
        "联系人和联系方式 董事会秘书 证券事务代表 姓名 许杭 何志超\n"
        "王  栋 董事长 男 52 2019-03-08 / 30 30 - / 50.76 否\n"
        "包  华 副董事长、总经理 男 42 2025-01-26 / - - - / - 否"
    )

    people = _extract_executives(text)
    by_name = {item["name"]: item["role"] for item in people}

    assert by_name["王栋"] in {"法定代表人", "董事长"}
    assert by_name["许杭"] == "董事会秘书"
    assert by_name["何志超"] == "证券事务代表"
    assert by_name["包华"] == "副董事长、总经理"


def test_new_material_replaces_placeholder_concept_content() -> None:
    old_content = "# 义支付\n\n## 概念说明\n相关概念\n\n## 关联对象\n该概念当前关联到 [[小商品城]] 的已解析材料。"
    old_meta = {"description": "相关概念"}
    new_meta = {"description": "义支付是小商品城围绕市场交易和支付结算场景建设的业务能力。"}

    assert _should_update_concept_content(old_content, old_meta, new_meta)


def test_web_enriched_content_is_appended_not_replaced() -> None:
    old_content = (
        "# 义支付\n\n## 概念说明\n义支付已有公开资料整理。\n\n"
        + "完整说明。" * 160
        + "\n\n## 联网来源\n- 来源 A"
    )
    old_meta = {"description": "义支付已有公开资料整理。", "web_enrichment": {"query": "义支付"}}
    new_meta = {"description": "年报披露义支付继续服务市场支付结算场景。"}

    assert not _should_update_concept_content(old_content, old_meta, new_meta)
    updated = _append_uploaded_material_update(
        old_content,
        new_meta,
        SourceDocument(
            filename="小商品城2025年年度报告.pdf",
            storage_uri="local://pytest",
            sha256="a" * 64,
            size_bytes=1,
            status="parsed",
        ),
    )
    assert "## 上传材料更新" in (updated or "")
    assert "支付结算场景" in (updated or "")


def test_clean_llm_answer_removes_thinking_block() -> None:
    assert _clean_llm_answer("<think>推理过程</think>\n\n正式回答") == "正式回答"
