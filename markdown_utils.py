import re
from typing import Any

MARKDOWN_V2_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!"


def escape_markdown_v2(text: Any) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return re.sub(f"([{re.escape(MARKDOWN_V2_SPECIAL_CHARS)}])", r"\\\1", text)
