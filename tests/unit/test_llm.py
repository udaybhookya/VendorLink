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


def test_usage_scopes_count_tokens_without_double_counting() -> None:
    from types import SimpleNamespace

    from vl_core.llm import record_usage, usage_scope

    meta = SimpleNamespace(
        prompt_token_count=100, candidates_token_count=20, thoughts_token_count=5
    )
    assert record_usage(meta) == (100, 25)  # outside any scope: counted, not stored
    with usage_scope() as outer:
        record_usage(meta)
        with usage_scope() as inner:  # a sub-agent's tokens are its own
            record_usage(meta)
            record_usage(None)
        record_usage(SimpleNamespace(prompt_token_count=None, candidates_token_count=1))
    assert inner.attrs() == {"tokens_in": 100, "tokens_out": 25, "model_calls": 1}
    assert outer.attrs() == {"tokens_in": 100, "tokens_out": 26, "model_calls": 2}
    with usage_scope() as empty:
        pass
    assert empty.attrs() == {}
