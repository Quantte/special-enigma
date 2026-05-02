from gitlab_notifier.notifier.events import EventKind
from gitlab_notifier.notifier.notification import Notification


def test_notification_construct():
    n = Notification(
        kind=EventKind.PUSH,
        repo_path="team/api",
        gitlab_project_id=1,
        actor="alice",
        title="3 commits to main",
        body="abc...",
        url="https://gitlab.example.com/team/api/-/commits/main",
    )
    assert n.kind == EventKind.PUSH
    assert n.repo_path == "team/api"
