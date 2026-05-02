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
