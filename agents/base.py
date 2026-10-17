"""Base class for every agent.

    class ProduceGrader(Agent):
        class Options(BaseModel):          # validated from config/agents.json "settings"
            llm_tier: Literal["flash", "pro"] = "flash"

        @skill("grade.produce")
        async def grade(self, inp: ListingStepIn) -> ListingStepOut: ...

The skill's input and output types must be the models named in the agent's card, so the card,
the code and the wire format cannot drift apart.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeVar, get_type_hints

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from vl_core.a2a import make_client
from vl_core.crypto import Signer
from vl_core.llm import ImageGen, Llm, get_image_gen, get_llm
from vl_core.obs import get_logger
from vl_core.schemas import AgentCard
from vl_core.settings import Settings
from vl_core.store import BlobStore, Repo, get_blob_store, get_repo

if TYPE_CHECKING:
    from agents.registry import AgentRegistry

SKILL_ATTR = "__vl_skill__"
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])
M = TypeVar("M", bound=BaseModel)


class AgentError(Exception):
    pass


def skill(capability: str) -> Callable[[F], F]:
    """Mark an async method `(self, inp: In) -> Out` as the handler for `capability`."""

    def mark(fn: F) -> F:
        setattr(fn, SKILL_ATTR, capability)
        return fn

    return mark


class NoOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class SkillHandler:
    capability: str
    method: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]


@dataclass
class AgentContext:
    """Everything an agent may use, injected by its host. Agents never reach for globals."""

    settings: Settings
    agent_id: str
    tenant: str
    registry: AgentRegistry | None = None
    signer: Signer | None = None  # only for agents whose card has the "sign" permission
    resources: dict[str, Any] = field(default_factory=dict)  # host-provided ports

    def llm(self, tier: Literal["flash", "pro"]) -> Llm:
        return get_llm(tier, self.settings)

    def images(self) -> ImageGen | None:
        """The image model, or None when IMAGE_MODEL is not set."""
        return get_image_gen(self.settings)

    def http(self, base_url: str) -> httpx.AsyncClient:
        return make_client(base_url)

    def blob(self) -> BlobStore:
        return get_blob_store(self.settings)

    def repo(self, model: type[M], name: str) -> Repo[M]:
        """Agent-private collection, namespaced by tenant and agent id."""
        return get_repo(model, f"{self.tenant}.{self.agent_id}.{name}", self.settings)

    def log(self) -> Any:
        return get_logger(f"agent.{self.agent_id}")


class Agent:
    Options: ClassVar[type[BaseModel]] = NoOptions
    _skills: ClassVar[dict[str, SkillHandler]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        skills = dict(getattr(cls, "_skills", {}))
        for name, fn in vars(cls).items():
            capability = getattr(fn, SKILL_ATTR, None)
            if capability is None:
                continue
            if not inspect.iscoroutinefunction(fn):
                raise AgentError(f"{cls.__name__}.{name} must be async")
            hints = get_type_hints(fn)
            params = [p for p in inspect.signature(fn).parameters if p != "self"]
            in_model = hints.get(params[0]) if len(params) == 1 else None
            out_model = hints.get("return")
            if not (_is_model(in_model) and _is_model(out_model)):
                raise AgentError(f"{cls.__name__}.{name} must be (self, inp: Model) -> Model")
            skills[capability] = SkillHandler(capability, name, in_model, out_model)  # type: ignore[arg-type]
        cls._skills = skills

    def __init__(self, card: AgentCard, ctx: AgentContext, options: BaseModel | None = None):
        self.card = card
        self.ctx = ctx
        # Validated instance of the class's Options (typed loosely so subclasses can narrow it).
        self.options: Any = options if options is not None else self.Options()
        problems = self.contract_problems(card)
        if problems:
            raise AgentError(f"{card.agent_id}: " + "; ".join(problems))

    @classmethod
    def contract_problems(cls, card: AgentCard) -> list[str]:
        """Differences between the card's skills and the class's @skill methods."""
        problems = []
        declared = {s.capability: s for s in card.skills}
        for cap in sorted(set(declared) ^ set(cls._skills)):
            where = "card" if cap in declared else "code"
            problems.append(f"skill {cap!r} only in the {where}")
        for cap in set(declared) & set(cls._skills):
            spec, handler = declared[cap], cls._skills[cap]
            if spec.input_schema != handler.input_model.__name__:
                problems.append(
                    f"{cap}: card input {spec.input_schema}, code {handler.input_model.__name__}"
                )
            if spec.output_schema != handler.output_model.__name__:
                problems.append(
                    f"{cap}: card output {spec.output_schema}, code {handler.output_model.__name__}"
                )
        return problems

    async def invoke(self, capability: str, payload: BaseModel | dict[str, Any]) -> BaseModel:
        handler = self._skills.get(capability)
        if handler is None:
            raise AgentError(f"{self.card.agent_id} has no skill {capability!r}")
        if isinstance(payload, handler.input_model):
            inp = payload
        else:
            data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
            try:
                inp = handler.input_model.model_validate(data)
            except ValidationError as exc:
                raise AgentError(
                    f"{capability}: bad input: {exc.errors(include_input=False)}"
                ) from exc
        result = await getattr(self, handler.method)(inp)
        if not isinstance(result, handler.output_model):
            raise AgentError(
                f"{capability} returned {type(result).__name__}, "
                f"expected {handler.output_model.__name__}"
            )
        return result


def _is_model(tp: object) -> bool:
    return isinstance(tp, type) and issubclass(tp, BaseModel)
