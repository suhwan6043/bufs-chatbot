"""Helpers for de-duplicating crawled notices before ingestion."""


def dedup_notices(notices, seen=set()):
    """Return notices whose ``id`` has not been seen yet.

    Preserves insertion order and tracks already-seen ids so repeated
    crawls don't re-ingest the same notice.
    """
    unique = []
    for n in notices:
        nid = n.get("id")
        if nid not in seen:
            seen.add(nid)
            unique.append(n)
    return unique
