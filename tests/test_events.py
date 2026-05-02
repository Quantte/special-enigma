from gitlab_notifier.notifier.events import EventKind, ALL_EVENTS, mask_has, mask_set


def test_eventkind_bitmask_unique():
    values = [e.value for e in EventKind]
    assert len(values) == len(set(values))


def test_all_events_covers_every_kind():
    expected = 0
    for e in EventKind:
        expected |= e.value
    assert ALL_EVENTS == expected


def test_mask_has_and_set():
    m = 0
    m = mask_set(m, EventKind.PUSH, True)
    assert mask_has(m, EventKind.PUSH)
    assert not mask_has(m, EventKind.MR_OPEN)
    m = mask_set(m, EventKind.PUSH, False)
    assert not mask_has(m, EventKind.PUSH)
