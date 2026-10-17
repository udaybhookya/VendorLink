from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts._stack import build_stack, teardown_stack
from vl_core.settings import Settings


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Settings]:
    yield build_stack(tmp_path)
    teardown_stack()
