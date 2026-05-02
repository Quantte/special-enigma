from enum import IntFlag, auto


class EventKind(IntFlag):
    PUSH = auto()
    MR_OPEN = auto()
    MR_UPDATE = auto()
    MR_MERGE = auto()
    MR_COMMENT = auto()
    MR_APPROVAL = auto()
    PIPELINE_FAIL = auto()
    ISSUE = auto()
    TAG = auto()


ALL_EVENTS: int = 0
for _e in EventKind:
    ALL_EVENTS |= _e.value


def mask_has(mask: int, kind: EventKind) -> bool:
    return bool(mask & kind.value)


def mask_set(mask: int, kind: EventKind, on: bool) -> int:
    return (mask | kind.value) if on else (mask & ~kind.value)


EVENT_NAMES: dict[str, EventKind] = {
    "push": EventKind.PUSH,
    "mr_open": EventKind.MR_OPEN,
    "mr_update": EventKind.MR_UPDATE,
    "mr_merge": EventKind.MR_MERGE,
    "mr_comment": EventKind.MR_COMMENT,
    "mr_approval": EventKind.MR_APPROVAL,
    "pipeline_fail": EventKind.PIPELINE_FAIL,
    "issue": EventKind.ISSUE,
    "tag": EventKind.TAG,
}
