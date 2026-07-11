from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from emercard.core.config import Settings
from emercard.db import Database
from emercard.main import create_app


class FakeDatabase(Database):
    def __init__(self, ready: bool) -> None:
        self.ready = ready
        self.started = False
        self.closed = False
        self.ping_count = 0

    async def start(self) -> None:
        self.started = True

    async def ping(self) -> bool:
        self.ping_count += 1
        return self.ready

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        debug=False,
        mongodb_uri="mongodb://localhost:27017",
        mongodb_database="emercard_test",
        cors_origins=["http://localhost:4321"],
        cors_allow_credentials=True,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[tuple[TestClient, FakeDatabase]]:
    database = FakeDatabase(ready=True)
    app = create_app(settings=settings, database=database)
    with TestClient(app) as test_client:
        yield test_client, database
