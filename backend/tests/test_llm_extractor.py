from app.services.llm_extractor import (
    DOCUMENT_CHAR_LIMIT,
    _build_prompt,
    _document_char_limit,
    _extract_markdown,
    _is_structured_markdown,
    _parse_json_object,
    _prepare_document_text,
)


def test_parse_json_object_strips_thinking_and_fences() -> None:
    raw = """<think>分析过程</think>
```json
{"metadata":{"title":"用友软件2001年年报解析"},"content_md":"# 用友软件2001年年报解析"}
```
"""
    parsed = _parse_json_object(raw)
    assert parsed["metadata"]["title"] == "用友软件2001年年报解析"


def test_ollama_document_limit_reserves_context_for_output() -> None:
    assert _document_char_limit("ollama", 32096) < 22000
    assert _document_char_limit("minimax", 32096) == DOCUMENT_CHAR_LIMIT
    prepared = _prepare_document_text("用友网络" * 10000, char_limit=6000)
    assert len(prepared) < 6200
    assert "中间内容已截断" in prepared


def test_extract_markdown_accepts_common_payload_shapes() -> None:
    payload = {
        "metadata": {"title": "测试公司2024年年报解析"},
        "sections": {
            "投资摘要": "收入承压。",
            "公司与报告概览": "年度报告。",
            "财务表现与质量观察": "现金流改善。",
            "分业务 / 产品趋势": "云业务增长。",
        },
    }

    markdown = _extract_markdown(payload)

    assert markdown.startswith("# 测试公司2024年年报解析")
    assert "## 投资摘要" in markdown
    assert _is_structured_markdown(markdown)


def test_raw_excerpt_fallback_is_not_structured_markdown() -> None:
    markdown = """# 用友网络科技股份有限公司 2024 年年度报告

## 摘要
<!-- page:1 --> 用友网络科技股份有限公司 2024 年年度报告

## 解析信息
- 文档类型: `annual-report`

## 原文摘录
<!-- page:1 --> 原始 PDF 文本
"""

    assert not _is_structured_markdown(markdown)


def test_build_prompt_includes_executive_schema_without_format_error(tmp_path) -> None:
    prompt = _build_prompt(
        text="公司的法定代表人 王栋。董事会秘书 许杭。",
        base_metadata={"title": "测试年报"},
        source_path=tmp_path / "report.pdf",
        document_char_limit=2000,
    )

    assert '"executives"' in prompt
    assert '"name": "姓名"' in prompt
