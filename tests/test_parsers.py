import json
from pathlib import Path

import pytest

from gitlab_notifier.notifier.events import EventKind
from gitlab_notifier.webhook.parsers import (
    parse_event,
    parse_issue,
    parse_merge_request,
    parse_note,
    parse_pipeline,
    parse_push,
    parse_tag_push,
)

FIX = Path(__file__).parent / "fixtures" / "gitlab"


def _load(name): return json.loads((FIX / name).read_text())


def test_parse_push():
    n = parse_push(_load("push.json"))
    assert n.kind == EventKind.PUSH
    assert n.repo_path == "team/api"
    assert n.gitlab_project_id == 1
    assert n.actor == "alice"
    assert "main" in n.title
    assert "2" in n.title
    assert "fix bug" in n.body
    assert n.url.endswith("/commits/main")


def test_parse_tag_push():
    n = parse_tag_push(_load("tag_push.json"))
    assert n.kind == EventKind.TAG
    assert "v1.2.3" in n.title
    assert n.actor == "bob"


@pytest.mark.parametrize("name,kind", [
    ("mr_open", EventKind.MR_OPEN),
    ("mr_update", EventKind.MR_UPDATE),
    ("mr_merge", EventKind.MR_MERGE),
    ("mr_approved", EventKind.MR_APPROVAL),
])
def test_parse_merge_request(name, kind):
    n = parse_merge_request(_load(f"{name}.json"))
    assert n is not None
    assert n.kind == kind
    assert n.repo_path == "team/api"
    assert "!7" in n.title or "Add login flow" in n.title
    assert n.url.endswith("/merge_requests/7")


def test_parse_mr_close_returns_none():
    payload = _load("mr_open.json")
    payload["object_attributes"]["action"] = "close"
    assert parse_merge_request(payload) is None


def test_parse_note_mr():
    n = parse_note(_load("note_mr.json"))
    assert n is not None and n.kind == EventKind.MR_COMMENT
    assert "!7" in n.title
    assert n.actor == "carol"


def test_parse_note_commit_returns_none():
    assert parse_note(_load("note_commit.json")) is None


def test_parse_pipeline_failed():
    n = parse_pipeline(_load("pipeline_failed.json"))
    assert n is not None and n.kind == EventKind.PIPELINE_FAIL
    assert "failed" in n.title.lower()


def test_parse_pipeline_success_returns_none():
    assert parse_pipeline(_load("pipeline_success.json")) is None


def test_parse_issue_open():
    n = parse_issue(_load("issue_open.json"))
    assert n is not None and n.kind == EventKind.ISSUE
    assert "#11" in n.title


def test_dispatch_unknown_returns_none():
    assert parse_event("Unknown Hook", {}) is None


def test_dispatch_push():
    assert parse_event("Push Hook", _load("push.json")) is not None


def test_dispatch_mr():
    assert parse_event("Merge Request Hook", _load("mr_open.json")) is not None
