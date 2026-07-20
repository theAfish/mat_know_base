import uuid
from datetime import datetime, timezone

import pytest

from mkb.models import Collection
from mkb.repositories import Collections


class _Repository:
    def __init__(self):
        self.row = Collection(
            id=uuid.uuid4(),
            name="Example",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.calls = []

    def get(self, collection_id):
        self.calls.append(("get", collection_id))
        return self.row if str(collection_id) == str(self.row.id) else None

    def list(self, *, limit, offset):
        self.calls.append(("list", limit, offset))
        return [self.row]


def test_collections_are_typed_and_json_serializable():
    repository = _Repository()
    collections = Collections(repository)

    result = collections.list(limit=20, offset=2)

    assert result == [repository.row]
    assert result[0].model_dump(mode="json")["id"] == str(repository.row.id)
    assert repository.calls == [("list", 20, 2)]


def test_collections_validate_pagination_and_get_optional_row():
    repository = _Repository()
    collections = Collections(repository)

    assert collections.get(repository.row.id) == repository.row
    assert collections.get(uuid.uuid4()) is None
    with pytest.raises(ValueError, match="between"):
        collections.list(limit=0)
    with pytest.raises(ValueError, match="non-negative"):
        collections.list(offset=-1)
