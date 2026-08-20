import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml


FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<yaml>.*?)\n---\s*\n?", re.DOTALL)
YEAR_RE = re.compile(r"(20\d{2})")
TICKER_RE = re.compile(r"\b(?:(SH|SZ|BJ)?\s?(\d{6})|(\d{6})\.(SH|SZ|BJ))\b", re.IGNORECASE)


def extract_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw_yaml = match.group("yaml")
    parsed = yaml.safe_load(raw_yaml) or {}
    if not isinstance(parsed, dict):
        parsed = {}
    return parsed, text[match.end() :]


def infer_metadata(text: str, source_path: Path, parser_name: str, parser_warnings: list[str]) -> dict[str, Any]:
    frontmatter, body = extract_frontmatter(text)
    title = frontmatter.get("title") or _infer_title(body, source_path)
    report_year = frontmatter.get("report_year") or _infer_year(title + "\n" + body)
    doc_type = frontmatter.get("type") or _infer_type(title, body, source_path)
    ticker = frontmatter.get("ticker") or _infer_ticker(title + "\n" + body)
    today = date.today().isoformat()
    quality = run_financial_reconciliation(body, frontmatter, doc_type)

    metadata: dict[str, Any] = {
        "title": title,
        "type": doc_type,
        "ticker": ticker,
        "report_year": report_year,
        "publish_date": frontmatter.get("publish_date") or today,
        "source_file": str(source_path),
        "tags": frontmatter.get("tags") or _infer_tags(body, doc_type),
        "related": frontmatter.get("related") or [],
        "created": frontmatter.get("created") or today,
        "updated": today,
        "analysis_status": "parsed",
        "credibility_score": float(frontmatter.get("credibility_score", 0.75)),
        "raw_content_ref": str(source_path),
        "parser": parser_name,
        "parser_warnings": parser_warnings,
        "parse_quality": quality["status"],
        "financial_reconciliation": quality,
    }
    metadata.update({key: value for key, value in frontmatter.items() if key not in metadata})
    return metadata


def build_markdown(text: str, metadata: dict[str, Any]) -> str:
    _, body = extract_frontmatter(text)
    excerpt = body.strip()
    if len(excerpt) > 8000:
        excerpt = excerpt[:8000].rstrip() + "\n\n..."
    lines = [
        f"# {metadata['title']}",
        "",
        "## 摘要",
        _build_summary(body),
        "",
        "## 解析信息",
        f"- 文档类型: `{metadata['type']}`",
        f"- 股票代码: `{metadata.get('ticker') or 'N/A'}`",
        f"- 报告年份: `{metadata.get('report_year') or 'N/A'}`",
        f"- 解析器: `{metadata.get('parser')}`",
        f"- 勾稽校验: `{metadata.get('parse_quality')}`",
        "",
        "## 原文摘录",
        excerpt or "_No text extracted._",
    ]
    return "\n".join(lines)


def compute_doc_hash(content: str, source_path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(str(source_path).encode("utf-8"))
    digest.update(b"\0")
    digest.update(content.encode("utf-8", errors="ignore"))
    return digest.hexdigest()


def run_financial_reconciliation(text: str, metadata: dict[str, Any], doc_type: str) -> dict[str, Any]:
    values = {
        "assets": _number_from_candidates(metadata, text, ["total_assets", "assets", "资产总计", "总资产"]),
        "liabilities": _number_from_candidates(metadata, text, ["total_liabilities", "liabilities", "负债合计", "总负债"]),
        "equity": _number_from_candidates(metadata, text, ["total_equity", "equity", "所有者权益", "股东权益"]),
    }
    if any(value is None for value in values.values()):
        return {
            "status": "skipped",
            "message": "financial totals were not sufficiently detected",
            "values": values,
        }
    assets = float(values["assets"])
    liabilities = float(values["liabilities"])
    equity = float(values["equity"])
    diff = abs(assets - liabilities - equity)
    tolerance = max(1.0, abs(assets) * 0.01)
    status = "ok" if diff <= tolerance else "warning"
    return {
        "status": status,
        "message": "assets equal liabilities plus equity within tolerance" if status == "ok" else "assets do not reconcile with liabilities plus equity",
        "values": values,
        "diff": diff,
        "tolerance": tolerance,
        "doc_type": doc_type,
    }


def _infer_title(text: str, source_path: Path) -> str:
    for line in text.splitlines():
        clean = line.strip(" #\t")
        if not clean or re.fullmatch(r"<!--\s*page:\d+\s*-->", clean):
            continue
        if clean:
            return clean[:120]
    return source_path.stem


def _infer_year(text: str) -> int | None:
    match = YEAR_RE.search(text)
    return int(match.group(1)) if match else None


def _infer_ticker(text: str) -> str | None:
    match = TICKER_RE.search(text)
    if not match:
        return None
    if match.group(2):
        market = (match.group(1) or "").upper()
        return f"{market}{match.group(2)}" if market else match.group(2)
    return f"{match.group(4).upper()}{match.group(3)}"


def _infer_type(title: str, text: str, source_path: Path) -> str:
    haystack = f"{title}\n{text[:2000]}\n{source_path.name}"
    if any(keyword in haystack for keyword in ("年报", "年度报告", "annual report")):
        return "annual-report"
    if any(keyword in haystack for keyword in ("风险", "诉讼", "监管问询", "内控")):
        return "risk-event"
    if any(keyword in haystack for keyword in ("高管", "董事", "监事", "履历")):
        return "personnel-profile"
    if any(keyword in haystack for keyword in ("营收", "毛利率", "分部", "业务板块")):
        return "segment-analysis"
    return "general-doc"


def _infer_tags(text: str, doc_type: str) -> list[str]:
    tags = [doc_type]
    keyword_map = {
        "财务": ("资产", "负债", "营收", "利润", "现金流"),
        "风险": ("诉讼", "担保", "处罚", "问询", "内控"),
        "战略": ("战略", "展望", "规划", "目标"),
        "治理": ("董事", "监事", "高管", "股权"),
    }
    for tag, keywords in keyword_map.items():
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    return tags


def _build_summary(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return "暂未抽取到可用文本。"
    return clean[:400] + ("..." if len(clean) > 400 else "")


def _number_from_candidates(metadata: dict[str, Any], text: str, keys: list[str]) -> float | None:
    for key in keys:
        if key in metadata:
            return _to_float(metadata[key])
    for key in keys:
        pattern = re.compile(rf"{re.escape(key)}[^\d\-\.]{{0,20}}([\-]?\d+(?:,\d{{3}})*(?:\.\d+)?)")
        match = pattern.search(text)
        if match:
            return _to_float(match.group(1))
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    clean = str(value).replace(",", "").strip()
    try:
        return float(clean)
    except ValueError:
        return None
