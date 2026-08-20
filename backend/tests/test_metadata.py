from pathlib import Path

from app.services.metadata import build_markdown, compute_doc_hash, infer_metadata, run_financial_reconciliation


def test_financial_reconciliation_ok() -> None:
    result = run_financial_reconciliation(
        "资产总计 1000 负债合计 400 所有者权益 600",
        {},
        "annual-report",
    )
    assert result["status"] == "ok"


def test_infer_metadata_from_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    text = """---
title: 测试公司2025年年报解析
type: annual-report
ticker: SH600000
---
资产总计 1000 负债合计 400 所有者权益 600
"""
    path.write_text(text, encoding="utf-8")
    metadata = infer_metadata(text, path, "plain-text", [])
    markdown = build_markdown(text, metadata)
    assert metadata["ticker"] == "SH600000"
    assert metadata["parse_quality"] == "ok"
    assert "# 测试公司2025年年报解析" in markdown
    assert len(compute_doc_hash(markdown, path)) == 64

