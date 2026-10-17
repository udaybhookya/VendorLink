"""Human in the loop: agents ask people for missing information, approvals and confirmations
over in-app, voice, Slack or Gmail, and the paused work resumes on the answer."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from vl_core.schemas.common import FALLBACK_LANGUAGE, VLModel

TaskKind = Literal["missing_info", "approval", "consent", "review", "notice"]
AnswerType = Literal["choice", "number", "text", "date"]
ChannelName = Literal["inapp", "slack", "gmail"]
TaskStatus = Literal["open", "answered", "expired", "cancelled"]


class Choice(VLModel):
    id: str = Field(description="Machine value, e.g. approve")
    label: str
    words: list[str] = Field(default_factory=list, description="Extra words that mean this choice")


class InfoRequest(VLModel):
    """What an agent needs from a person to continue. Emitted by specialists and jobs."""

    field: str = Field(description="What is missing, e.g. price, qty, doc:aadhaar")
    ask: str = Field(description="Role to ask: vendor, buyer, category_manager, ops")
    kind: TaskKind = "missing_info"
    answer_type: AnswerType = "text"
    question_key: str = Field(description="i18n key for the question, e.g. hitl.q.price")
    params: dict[str, str | int | float] = Field(default_factory=dict)
    choices: list[Choice] = Field(default_factory=list)


class AskRequest(VLModel):
    """human.ask: put a question to a party's person in a role."""

    party: str = Field(description="vendor:… or retailer:…")
    request: InfoRequest
    language: str = FALLBACK_LANGUAGE
    subject_ref: str = Field(description="What it is about: order id, listing id, job id …")
    resume: str = Field(description="Who continues on the answer, e.g. job:<id>, queue:<id>")
    trace_id: str = ""
    deadline_minutes: int | None = None
    task_id: str | None = Field(default=None, description="Idempotency: same id → same task")


class Delivery(VLModel):
    channel: ChannelName
    to: str
    message_id: str = ""
    thread_id: str = ""
    sent_at: datetime
    reminder: int = 0


class HumanTask(VLModel):
    id: str
    token: str = Field(description="Short code that ties a reply to this task, e.g. VL-7KQ2")
    party: str
    role: str
    kind: TaskKind
    request: InfoRequest
    question: str = Field(description="Rendered in the recipient's language")
    language: str
    subject_ref: str
    resume: str
    status: TaskStatus = "open"
    deliveries: list[Delivery] = Field(default_factory=list)
    created_at: datetime
    deadline: datetime | None = None
    escalation_level: int = 0
    answer: str | None = None
    answer_value: Any = None
    answered_by: str | None = None
    answered_via: ChannelName | None = None
    answered_at: datetime | None = None
    trace_id: str = ""
    seen_replies: list[str] = Field(default_factory=list)


class AnswerIn(VLModel):
    """human.answer: a reply from any channel. Parsed deterministically against the request."""

    task_id: str
    text: str
    by: str = Field(description="Who answered: user id, email or vendor id")
    via: ChannelName


class TaskRef(VLModel):
    task_id: str
    reason: str = ""
    outcome: Literal["cancelled", "answered"] = "cancelled"


class TaskQuery(VLModel):
    party: str | None = None
    status: TaskStatus | None = None
    subject_ref: str | None = None


class TaskList(VLModel):
    tasks: list[HumanTask]


class TickIn(VLModel):
    """Poll channels for replies, send reminders, escalate, expire."""

    party: str | None = None


class TickOut(VLModel):
    answered: list[str] = Field(default_factory=list)
    reminded: list[str] = Field(default_factory=list)
    escalated: list[str] = Field(default_factory=list)
    expired: list[str] = Field(default_factory=list)


# ---- channels ------------------------------------------------------------------------------


class ChannelMessage(VLModel):
    channel: ChannelName
    to: str = Field(description="Slack channel/user id, email address, or party id for in-app")
    subject: str = ""
    text: str
    thread_id: str = Field(default="", description="Reply in this thread (Slack ts, mail thread)")
    token: str = Field(default="", description="Task token, embedded so replies can be matched")
    link: str = ""


class ChannelReceipt(VLModel):
    channel: ChannelName
    message_id: str
    thread_id: str = ""
    simulated: bool = False


class PollRequest(VLModel):
    channel: ChannelName
    to: str
    thread_id: str = ""
    token: str = ""
    since: datetime | None = None


class ChannelReply(VLModel):
    message_id: str
    text: str
    by: str
    at: datetime | None = None


class ChannelReplies(VLModel):
    replies: list[ChannelReply] = Field(default_factory=list)


class OutboxMessage(VLModel):
    """Local channel fallback: what would have gone to Slack / Gmail, and replies typed back."""

    id: str
    message: ChannelMessage
    sent_at: datetime
    replies: list[ChannelReply] = Field(default_factory=list)


# ---- routing config (config/hitl/routing.json) -----------------------------------------------


class Step(VLModel):
    after_minutes: int = Field(ge=0)
    channels: list[ChannelName]
    role: str | None = Field(default=None, description="Escalate to another role")


class RoutingRule(VLModel):
    kind: TaskKind
    role: str
    steps: list[Step] = Field(min_length=1, description="First step is the initial delivery")
    expire_after_minutes: int | None = None
    on_expire: Literal["cancel", "reject", "keep"] = "cancel"


class RoutingConfig(VLModel):
    id: str
    version: int = Field(ge=1)
    rules: list[RoutingRule]

    def rule(self, kind: str, role: str) -> RoutingRule | None:
        return next((r for r in self.rules if r.kind == kind and r.role == role), None) or next(
            (r for r in self.rules if r.kind == kind and r.role == "*"), None
        )


class Contact(VLModel):
    """Where one role at one party can be reached. Addresses may use ${ENV} placeholders."""

    role: str
    name: str = ""
    slack: str | None = None
    email: str | None = None
    inapp: bool = False


class ContactBook(VLModel):
    party: str
    note: str = ""
    language: str = Field(
        default=FALLBACK_LANGUAGE, description="Language this party's staff are written to in"
    )
    contacts: list[Contact]

    def for_role(self, role: str) -> Contact | None:
        return next((c for c in self.contacts if c.role == role), None)


# ---- cross-tenant asks and jobs ----------------------------------------------------------------


class InfoRequestMessage(VLModel):
    """Retailer → vendor (gateway skill info.request): please ask your vendor this."""

    request_id: str
    retailer_id: str
    vendor_id: str
    request: InfoRequest
    subject_ref: str


class InfoResponseMessage(VLModel):
    """Vendor → retailer (gateway skill info.response): her answer."""

    request_id: str
    retailer_id: str
    vendor_id: str
    answer: str
    answer_value: Any = None
    status: Literal["answered", "expired", "cancelled"] = "answered"


class JobRun(VLModel):
    id: str
    kind: str
    retailer_id: str
    status: Literal["running", "waiting", "done", "failed"]
    started_at: datetime
    updated_at: datetime
    waiting_on: list[str] = Field(default_factory=list, description="Open human task ids")
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class ReplenishmentRunIn(VLModel):
    for_date: str = Field(description="ISO date, or 'today' / 'tomorrow' (retailer's timezone)")
    stores: list[str] | None = None
    vendor_id: str


# ---- channel → MCP mapping (config/channels/<channel>.json) --------------------------------


class McpOperation(VLModel):
    """One channel operation expressed as an MCP tool call. Strings in `args` may use
    {to} {text} {subject} {thread_id} {token} placeholders."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    returns: dict[str, str] = Field(default_factory=dict, description="our field → result path")
    items: str | None = Field(default=None, description="Path to the list of messages (read)")
    item: dict[str, str] = Field(default_factory=dict, description="our field → item path")
    skip_if: dict[str, str] = Field(default_factory=dict, description="Skip items with this path")


class ChannelConfig(VLModel):
    id: str
    version: int = Field(ge=1)
    note: str = ""
    server: str = Field(description="MCP server name: settings come from MCP_<SERVER>_* env")
    operations: dict[str, McpOperation]


class QuestionAnswer(VLModel):
    text: str = Field(min_length=1)
    by: str = ""
