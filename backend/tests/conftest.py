from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

import pytest


@pytest.fixture
def make_async_iterable() -> Callable[[Iterable[Any]], AsyncIterator[Any]]:
    async def _make_async_iterable(items: Iterable[Any]) -> AsyncIterator[Any]:
        for item in items:
            yield item

    return _make_async_iterable
