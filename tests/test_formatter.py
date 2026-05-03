from gitlab_notifier.notifier.events import EventKind
from gitlab_notifier.notifier.formatter import escape_md, format_notification
from gitlab_notifier.notifier.notification import Notification


def test_escape_md():
    assert escape_md("a_b*c") == r"a\_b\*c"


def test_format_includes_repo_actor_link_title():
    n = Notification(
        kind=EventKind.PUSH, repo_path="team/api", gitlab_project_id=1,
        actor="alice", title="2 commits to main", body="- abc commit", url="https://x/y",
    )
    out = format_notification(n)
    assert "team/api" in out
    assert "alice" in out
    assert "2 commits to main" in out
    assert "https://x/y" in out
    assert "🚀" in out


def test_format_uses_blockquote_for_comments():
    n = Notification(
        kind=EventKind.MR_COMMENT, repo_path="team/api", gitlab_project_id=1,
        actor="carol", title="Comment on MR !7", body="lgtm", url="https://x/y",
    )
    out = format_notification(n)
    assert "💬" in out
    assert ">lgtm" in out


def test_format_per_event_icons():
    from gitlab_notifier.notifier.events import EVENT_ICONS
    for kind, icon in EVENT_ICONS.items():
        n = Notification(kind=kind, repo_path="r", gitlab_project_id=1,
                         actor="a", title="t", body="", url="u")
        assert icon in format_notification(n)
