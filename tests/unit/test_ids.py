from jarvis.core.ids import uuid7


def test_uuid7_is_versioned_and_sorts_in_creation_order_within_a_millisecond():
    ids = [uuid7() for _ in range(500)]
    assert all(u.version == 7 for u in ids)
    assert len(set(ids)) == 500
    assert ids == sorted(ids)
