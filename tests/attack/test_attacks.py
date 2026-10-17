from scripts import attack_impostor, attack_injected_doc
from vl_core.settings import Settings


async def test_impostor_is_blocked(stack: Settings) -> None:
    await attack_impostor.run(stack)


async def test_injected_document_is_blocked(stack: Settings) -> None:
    await attack_injected_doc.run(stack)
