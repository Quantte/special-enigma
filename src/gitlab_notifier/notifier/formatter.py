from .events import EVENT_ICONS, EventKind
from .notification import Notification

_MD_SPECIALS = r"_*[]()~`>#+-=|{}.!\\"


def escape_md(text: str) -> str:
    out = []
    for ch in text:
        if ch in _MD_SPECIALS:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _blockquote(text: str) -> str:
    """MarkdownV2 blockquote: every line prefixed with > (escaped)."""
    return "\n".join(f">{line}" for line in text.splitlines() if line)


def format_notification(n: Notification) -> str:
    icon = EVENT_ICONS.get(n.kind, "🔔")
    title = escape_md(n.title)
    repo = escape_md(n.repo_path)
    actor = escape_md(n.actor)

    sections: list[str] = []
    sections.append(f"{icon} *{title}*")
    sections.append(f"`{repo}` · {actor}")

    if n.body:
        if n.kind == EventKind.MR_COMMENT:
            sections.append(_blockquote(escape_md(n.body)))
        elif n.kind == EventKind.PUSH:
            sections.append(escape_md(n.body))
        else:
            sections.append(escape_md(n.body))

    sections.append(f"[Open in GitLab]({n.url})")

    return "\n\n".join(sections)
