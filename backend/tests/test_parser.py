from pathlib import Path

from app.services.parser import parse_document


def test_plain_text_parser_returns_spans_and_quality(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("第一段\n\n第二段", encoding="utf-8")

    parsed = parse_document(path)

    assert parsed.parser_name == "plain-text"
    assert parsed.quality["status"] == "ok"
    assert parsed.quality["needs_ocr"] is False
    assert [span["type"] for span in parsed.spans] == ["paragraph", "paragraph"]

