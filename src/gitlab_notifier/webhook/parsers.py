from gitlab_notifier.notifier.events import EventKind
from gitlab_notifier.notifier.notification import Notification


def _project(payload: dict) -> tuple[int, str, str]:
    p = payload["project"]
    return p["id"], p["path_with_namespace"], p["web_url"]


def parse_push(payload: dict) -> Notification:
    pid, path, web_url = _project(payload)
    branch = payload["ref"].removeprefix("refs/heads/")
    count = payload.get("total_commits_count", len(payload.get("commits", [])))
    actor = payload.get("user_username") or payload.get("user_name") or "unknown"
    body_lines = []
    for c in payload.get("commits", [])[:5]:
        first = c["message"].splitlines()[0][:100]
        body_lines.append(f"- {c['id'][:8]} {first}")
    return Notification(
        kind=EventKind.PUSH,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"{count} commit(s) to {branch}",
        body="\n".join(body_lines),
        url=f"{web_url}/-/commits/{branch}",
    )


def parse_tag_push(payload: dict) -> Notification:
    pid, path, web_url = _project(payload)
    tag = payload["ref"].removeprefix("refs/tags/")
    actor = payload.get("user_username") or payload.get("user_name") or "unknown"
    return Notification(
        kind=EventKind.TAG,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"tag {tag} pushed",
        body="",
        url=f"{web_url}/-/tags/{tag}",
    )


_MR_ACTION_TO_KIND = {
    "open": EventKind.MR_OPEN,
    "reopen": EventKind.MR_OPEN,
    "update": EventKind.MR_UPDATE,
    "merge": EventKind.MR_MERGE,
    "approved": EventKind.MR_APPROVAL,
}


def parse_merge_request(payload: dict) -> Notification | None:
    pid, path, _ = _project(payload)
    attrs = payload["object_attributes"]
    action = attrs.get("action")
    kind = _MR_ACTION_TO_KIND.get(action)
    if kind is None:
        return None
    actor = (payload.get("user") or {}).get("username") or "unknown"
    iid = attrs["iid"]
    title = attrs["title"]
    src = attrs.get("source_branch", "")
    tgt = attrs.get("target_branch", "")
    verb = {
        EventKind.MR_OPEN: "opened",
        EventKind.MR_UPDATE: "updated",
        EventKind.MR_MERGE: "merged",
        EventKind.MR_APPROVAL: "approved",
    }[kind]
    return Notification(
        kind=kind,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"MR !{iid} {verb}: {title}",
        body=f"{src} → {tgt}" if src and tgt else "",
        url=attrs["url"],
    )


def parse_note(payload: dict) -> Notification | None:
    attrs = payload["object_attributes"]
    if attrs.get("noteable_type") != "MergeRequest":
        return None
    pid, path, _ = _project(payload)
    actor = (payload.get("user") or {}).get("username") or "unknown"
    mr = payload.get("merge_request") or {}
    iid = mr.get("iid", "?")
    mr_title = mr.get("title", "")
    snippet = (attrs.get("note") or "").splitlines()[0][:200]
    return Notification(
        kind=EventKind.MR_COMMENT,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"comment on MR !{iid}: {mr_title}",
        body=snippet,
        url=attrs["url"],
    )


def parse_pipeline(payload: dict) -> Notification | None:
    attrs = payload["object_attributes"]
    if attrs.get("status") != "failed":
        return None
    pid, path, web_url = _project(payload)
    actor = (payload.get("user") or {}).get("username") or "unknown"
    pipeline_id = attrs.get("id")
    ref = attrs.get("ref", "")
    return Notification(
        kind=EventKind.PIPELINE_FAIL,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"pipeline #{pipeline_id} failed on {ref}",
        body="",
        url=f"{web_url}/-/pipelines/{pipeline_id}",
    )


def parse_issue(payload: dict) -> Notification | None:
    attrs = payload["object_attributes"]
    action = attrs.get("action")
    if action not in {"open", "reopen", "close"}:
        return None
    pid, path, _ = _project(payload)
    actor = (payload.get("user") or {}).get("username") or "unknown"
    iid = attrs["iid"]
    verb = {"open": "opened", "reopen": "reopened", "close": "closed"}[action]
    return Notification(
        kind=EventKind.ISSUE,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"issue #{iid} {verb}: {attrs['title']}",
        body="",
        url=attrs["url"],
    )


_EVENT_DISPATCH = {
    "Push Hook": parse_push,
    "Tag Push Hook": parse_tag_push,
    "Merge Request Hook": parse_merge_request,
    "Note Hook": parse_note,
    "Pipeline Hook": parse_pipeline,
    "Issue Hook": parse_issue,
}


def parse_event(event_header: str, payload: dict) -> Notification | None:
    fn = _EVENT_DISPATCH.get(event_header)
    if fn is None:
        return None
    return fn(payload)
