import uuid

import pytest

from app import vectors
from app.main import check_qdrant

pytestmark = pytest.mark.skipif(not check_qdrant(), reason="qdrant not reachable")


@pytest.fixture
def collection():
    """A throwaway 3-d collection, deleted after the test."""
    name = f"test_{uuid.uuid4().hex[:8]}"
    vectors.create_collection(name, 3, {"lang": vectors.PayloadSchemaType.KEYWORD})
    yield name
    vectors.client().delete_collection(name)


def test_upsert_search_and_filter(collection):
    ids = [str(uuid.uuid4()) for _ in range(3)]  # Postgres-style UUID ids
    vectors.upsert(collection, ids[0], [1, 0, 0], {"lang": "hi"})
    vectors.upsert(collection, ids[1], [0, 1, 0], {"lang": "en"})
    vectors.upsert(collection, ids[2], [0.9, 0.1, 0], {"lang": "en"})

    hits = vectors.search(collection, [1, 0, 0], k=3)
    assert hits[0].id == ids[0]                   # exact match is nearest
    assert [h.id for h in hits][1] == ids[2]      # then the almost-same one

    hits = vectors.search(collection, [1, 0, 0], k=3, filters={"lang": "en"})
    assert [h.id for h in hits] == [ids[2], ids[1]]  # the "hi" point is filtered out
