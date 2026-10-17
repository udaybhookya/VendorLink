from pathlib import Path

import pytest
from pydantic import BaseModel

from vl_core.llm import FakeLlm, get_tool_chat, load_prompt
from vl_core.settings import Settings


class Grade(BaseModel):
    grade: str
    confidence: float


async def test_valid_first_answer() -> None:
    llm = FakeLlm(['{"grade": "A", "confidence": 0.9}'])
    result = await llm.generate_json("grade this", Grade)
    assert not result.needs_review and result.value == Grade(grade="A", confidence=0.9)


async def test_one_retry_then_success() -> None:
    llm = FakeLlm(['{"grade": "A"}', '{"grade": "B", "confidence": 0.7}'])
    result = await llm.generate_json("grade this", Grade)
    assert result.value == Grade(grade="B", confidence=0.7)
    assert "rejected" in llm.prompts[1]


async def test_two_failures_need_review_not_default() -> None:
    llm = FakeLlm(["not json", '{"grade": 1}'])
    result = await llm.generate_json("grade this", Grade)
    assert result.needs_review and result.value is None and result.error


async def test_backend_error_needs_review() -> None:
    result = await FakeLlm([]).generate_json("grade this", Grade)
    assert result.needs_review and "RuntimeError" in (result.error or "")


def test_load_prompt(tmp_path: Path) -> None:
    (tmp_path / "concierge.te.md").write_text("నమస్తే", encoding="utf-8")
    assert load_prompt("concierge", "te", tmp_path) == "నమస్తే"
    with pytest.raises(FileNotFoundError):
        load_prompt("concierge", "id", tmp_path)


def test_tool_chat_is_reused_so_the_conversation_persists() -> None:
    settings = Settings(pro_model="some-model")
    first = get_tool_chat(settings)
    assert first is not None and get_tool_chat(settings) is first
