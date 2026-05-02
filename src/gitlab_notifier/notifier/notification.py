from dataclasses import dataclass

from .events import EventKind


@dataclass(frozen=True, slots=True)
class Notification:
    kind: EventKind
    repo_path: str
    gitlab_project_id: int
    actor: str
    title: str
    body: str
    url: str
