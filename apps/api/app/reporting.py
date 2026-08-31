import re
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urlsplit

from app.models import ContentItem


def inline_text(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def safe_markdown(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("<", "&lt;").replace(">", "&gt;")
    for character in "`*_[]|":
        escaped = escaped.replace(character, f"\\{character}")
    if re.match(r"^\s*(?:#{1,6}\s|[-+>]\s|\d+\.\s|---+$)", escaped):
        escaped = f"\\{escaped}"
    return escaped.strip()


def safe_url(value: str) -> str:
    if "\\" in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        return ""
    try:
        decoded = unquote(value)
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return ""
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = f"{hostname}:{port}" if port is not None else hostname
    path = quote(unquote(parsed.path), safe="/:@-._~!$&'+,;=")
    query = quote(unquote(parsed.query), safe="=&:@/?-._~!$'+,;")
    fragment = quote(unquote(parsed.fragment), safe="-._~!$&'+,;=:@/?")
    result = f"{parsed.scheme.lower()}://{authority}{path}"
    if query:
        result += f"?{query}"
    if fragment:
        result += f"#{fragment}"
    return result


def bullet_list(values: list[str]) -> str:
    if not values:
        return "- 暂无"
    return "\n".join(f"- {safe_markdown(inline_text(value))}" for value in values)


def report_date(item: ContentItem) -> str:
    value = item.published_at or item.fetched_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d")


def render_markdown_report(items: list[ContentItem], generated_at: datetime | None = None) -> str:
    generated = generated_at or datetime.now(UTC)
    lines = [
        "# 软件测试技术情报报告",
        "",
        "> 聚焦可融入软件测试与质量工程高价值场景的前沿技术动态。",
        "",
        f"- **生成时间**：{generated.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- **情报数量**：共 {len(items)} 条测试情报",
        "",
        "---",
    ]
    for index, item in enumerate(items, start=1):
        title = safe_markdown(inline_text(item.title))
        summary = safe_markdown(inline_text(item.analysis_summary or "暂无分析摘要"))
        value_analysis = safe_markdown(
            inline_text(item.testing_value_analysis or "暂无测试价值分析")
        )
        tags = (
            "、".join(safe_markdown(inline_text(tag)) for tag in (item.analysis_tags or []))
            or "暂无"
        )
        lines.extend(
            [
                "",
                f"## {index}. {title}",
                "",
                f"- **来源**：{safe_markdown(inline_text(item.source.name))}",
                f"- **发布日期**：{report_date(item)}",
                f"- **测试相关性**：{item.testing_relevance_score or 0}/100",
                f"- **测试价值**：{item.testing_value_score or 0}/100",
                f"- **标签**：{tags}",
                "",
                "### 情报摘要",
                "",
                summary,
                "",
                "### 测试价值分析",
                "",
                value_analysis,
                "",
                "### 适用测试场景",
                "",
                bullet_list(item.applicable_scenarios or []),
                "",
                "### 场景落地建议",
                "",
                bullet_list(item.adoption_suggestions or []),
                "",
                "### 风险与边界",
                "",
                bullet_list(item.analysis_risks or []),
                "",
                (
                    f"**原文链接**：[查看原文]({safe_url(item.url)})"
                    if safe_url(item.url)
                    else "**原文链接**：无有效 HTTP/HTTPS 链接"
                ),
                "",
                "---",
            ]
        )
    lines.extend(
        [
            "",
            "_本报告由测试技术情报雷达自动整理，评分与建议应结合实际系统风险进行人工复核。_",
            "",
        ]
    )
    return "\n".join(lines)
