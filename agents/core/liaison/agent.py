"""The Human Liaison: agents ask, people answer on whatever channel suits them, work resumes.

Host resources (set by vendor-svc / retailer-svc):
  contacts       async (party) -> ContactBook       who holds which role, and where
  hitl_resolved  async (HumanTask) -> None          resume the work waiting on this task
  clock          () -> datetime                     (tests)
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel

from agents.base import Agent, AgentContext, skill
from agents.core.liaison.answers import parse
from agents.resources import CLOCK, CONTACTS, RESOLVED
from vl_core.errors import Problem
from vl_core.i18n import Strings
from vl_core.obs import get_logger
from vl_core.schemas import (
    FALLBACK_LANGUAGE,
    AgentCard,
    AnswerIn,
    AskRequest,
    ChannelMessage,
    ChannelReceipt,
    ChannelReplies,
    Contact,
    ContactBook,
    Delivery,
    HumanTask,
    PollRequest,
    RoutingConfig,
    RoutingRule,
    Step,
    TaskList,
    TaskQuery,
    TaskRef,
    TickIn,
    TickOut,
    expand_env,
)

log = get_logger("agent.liaison")
DEFAULT_RULE = RoutingRule(
    kind="missing_info", role="*", steps=[Step(after_minutes=0, channels=["inapp"])]
)


class Liaison(Agent):
    def __init__(self, card: AgentCard, ctx: AgentContext, options: BaseModel | None = None):
        super().__init__(card, ctx, options)
        self.tasks = ctx.repo(HumanTask, "tasks")
        path = ctx.settings.vl_config_dir / "hitl" / "routing.json"
        self.routing = RoutingConfig.model_validate_json(path.read_text())

    # ---- plumbing ---------------------------------------------------------------------------

    def now(self) -> datetime:
        clock = self.ctx.resources.get(CLOCK)
        return cast(datetime, clock()) if clock else datetime.now(UTC)

    def rule(self, task: HumanTask) -> RoutingRule:
        return self.routing.rule(task.kind, task.role) or DEFAULT_RULE

    async def contacts(self, party: str) -> ContactBook:
        provider = self.ctx.resources.get(CONTACTS)
        if provider is None:
            return ContactBook(party=party, contacts=[Contact(role="vendor", inapp=True)])
        return cast(ContactBook, await provider(party))

    def strings(self, language: str) -> Strings:
        try:
            return Strings(language, self.ctx.settings.vl_config_dir / "i18n")
        except FileNotFoundError:
            return Strings(FALLBACK_LANGUAGE, self.ctx.settings.vl_config_dir / "i18n")

    async def resolved(self, task: HumanTask) -> None:
        hook = cast(Callable[[HumanTask], Awaitable[None]] | None, self.ctx.resources.get(RESOLVED))
        if hook is not None:
            await hook(task)

    def link(self, task: HumanTask) -> str:
        """Where the person can answer in a browser: the console for staff, the app for vendors."""
        base = self.ctx.settings.vl_public_url
        if task.party.startswith("vendor:"):
            return f"{base}/vendor?vendor={task.party}&tab=deals"
        return f"{base}/retailer/tasks?task={task.id}"

    def render(self, task: HumanTask, *, reminder: bool, unclear: bool = False) -> tuple[str, str]:
        s = self.strings(task.language)
        choices = " / ".join(c.label for c in task.request.choices)
        lines = []
        if unclear:
            lines.append(s.t("hitl.msg.unclear", choices=choices or "…"))
        else:
            lines.append(
                f"{s.t('hitl.msg.reminder')}: {task.question}" if reminder else task.question
            )
            if choices:
                lines.append(s.t("hitl.msg.reply_hint", choices=choices))
        lines.append(s.t("hitl.msg.open_link", link=self.link(task)))
        lines.append(f"[{task.token}]")
        title = task.question if len(task.question) <= 70 else task.question[:67] + "…"
        return s.t("hitl.msg.subject", token=task.token, title=title), "\n".join(lines)

    async def deliver(
        self, task: HumanTask, step_index: int, *, unclear: bool = False
    ) -> list[Delivery]:
        rule = self.rule(task)
        step = rule.steps[min(step_index, len(rule.steps) - 1)]
        role = step.role or task.role
        contact = (await self.contacts(task.party)).for_role(role)
        if contact is None:
            log.warning("no contact for role; nobody was asked", task=task.id, role=role)
            return []
        registry = self.ctx.registry
        assert registry is not None
        subject, text = self.render(task, reminder=step_index > 0 and not unclear, unclear=unclear)
        delivered = []
        for channel in step.channels:
            address = {
                "slack": contact.slack,
                "gmail": contact.email,
                "inapp": task.party if contact.inapp else None,
            }[channel]
            to = expand_env(address) if address else ""
            if not to:
                continue
            prior = next((d for d in task.deliveries if d.channel == channel and d.to == to), None)
            receipt = await registry.call(
                f"channel.{channel}.send",
                ChannelMessage(
                    channel=channel,
                    to=to,
                    subject=subject,
                    text=text,
                    thread_id=prior.thread_id if prior and channel == "slack" else "",
                    token=task.token,
                    link=self.link(task),
                ),
                ChannelReceipt,
            )
            log.info(
                f"asked {role} on {channel}",
                task=task.id,
                kind=task.kind,
                reminder=step_index or None,
                unclear=unclear or None,
                message_id=receipt.message_id,
            )
            delivered.append(
                Delivery(
                    channel=channel,
                    to=to,
                    message_id=receipt.message_id,
                    thread_id=receipt.thread_id or (prior.thread_id if prior else ""),
                    sent_at=self.now(),
                    reminder=step_index,
                )
            )
        return delivered

    async def _get(self, task_id: str) -> HumanTask:
        task = await self.tasks.get(task_id)
        if task is None:
            raise Problem(404, "unknown_task", task_id)
        return task

    async def _record_answer(self, task: HumanTask, text: str, by: str, via: Any) -> HumanTask:
        ok, value, note = parse(text, task.request, self.strings(task.language))
        if not ok:
            log.warning("answer unclear; asking again", task=task.id, via=via, note=note)
            raise Problem(422, "unclear_answer", note)
        done = task.model_copy(
            update={
                "status": "answered",
                "answer": text.strip()[:1000],
                "answer_value": value,
                "answered_by": by,
                "answered_via": via,
                "answered_at": self.now(),
            }
        )
        await self.tasks.put(done.id, done)
        log.info("answer recorded", task=task.id, kind=task.kind, via=via)
        await self.resolved(done)
        return done

    # ---- skills -------------------------------------------------------------------------------

    @skill("human.ask")
    async def ask(self, inp: AskRequest) -> HumanTask:
        if inp.task_id and (existing := await self.tasks.get(inp.task_id)) is not None:
            return existing  # idempotent: a resumed job may ask again
        s = self.strings(inp.language)
        rule = self.routing.rule(inp.request.kind, inp.request.ask) or DEFAULT_RULE
        minutes = inp.deadline_minutes or rule.expire_after_minutes
        now = self.now()
        task = HumanTask(
            id=inp.task_id or f"task-{uuid.uuid4().hex[:10]}",
            token=f"VL-{secrets.token_hex(3).upper()}",
            party=inp.party,
            role=inp.request.ask,
            kind=inp.request.kind,
            request=inp.request,
            question=s.t(inp.request.question_key, **inp.request.params),
            language=inp.language,
            subject_ref=inp.subject_ref,
            resume=inp.resume,
            created_at=now,
            deadline=now + timedelta(minutes=minutes) if minutes else None,
            trace_id=inp.trace_id,
        )
        task = task.model_copy(update={"deliveries": await self.deliver(task, 0)})
        if task.kind == "notice":  # tell, don't wait
            task = task.model_copy(
                update={"status": "answered", "answer": "notified", "answered_at": now}
            )
        await self.tasks.put(task.id, task)
        return task

    @skill("human.answer")
    async def answer(self, inp: AnswerIn) -> HumanTask:
        task = await self._get(inp.task_id)
        if task.status != "open":
            raise Problem(409, "task_closed", task.status)
        return await self._record_answer(task, inp.text, inp.by, inp.via)

    @skill("human.cancel")
    async def cancel(self, inp: TaskRef) -> HumanTask:
        task = await self._get(inp.task_id)
        if task.status != "open":
            return task
        closed = task.model_copy(
            update={"status": inp.outcome, "answer": inp.reason or None, "answered_at": self.now()}
        )
        await self.tasks.put(closed.id, closed)
        log.info("task closed", task=task.id, outcome=inp.outcome)
        return closed

    @skill("human.list")
    async def list_tasks(self, inp: TaskQuery) -> TaskList:
        tasks = await self.tasks.query()
        tasks = [
            t
            for t in tasks
            if (inp.party is None or t.party == inp.party)
            and (inp.status is None or t.status == inp.status)
            and (inp.subject_ref is None or t.subject_ref == inp.subject_ref)
        ]
        return TaskList(tasks=sorted(tasks, key=lambda t: t.created_at, reverse=True))

    @skill("human.tick")
    async def tick(self, inp: TickIn) -> TickOut:
        out = TickOut()
        registry = self.ctx.registry
        assert registry is not None
        for task in await self.tasks.query(status="open"):
            if inp.party is not None and task.party != inp.party:
                continue
            if await self._poll(task, out):
                continue
            now = self.now()
            rule = self.rule(task)
            if task.deadline is not None and now >= task.deadline:
                expired = task.model_copy(
                    update={"status": "expired", "answered_at": now, "answer_value": rule.on_expire}
                )
                await self.tasks.put(task.id, expired)
                log.info("task expired", task=task.id, on_expire=rule.on_expire)
                out.expired.append(task.id)
                if rule.on_expire != "keep":
                    await self.resolved(expired)
                continue
            nxt = task.escalation_level + 1
            if nxt < len(rule.steps) and now - task.created_at >= timedelta(
                minutes=rule.steps[nxt].after_minutes
            ):
                sent = await self.deliver(task, nxt)
                await self.tasks.put(
                    task.id,
                    task.model_copy(
                        update={"escalation_level": nxt, "deliveries": [*task.deliveries, *sent]}
                    ),
                )
                (out.escalated if rule.steps[nxt].role else out.reminded).append(task.id)
        return out

    async def _poll(self, task: HumanTask, out: TickOut) -> bool:
        """Read replies on Slack/Gmail deliveries; answer the task with the first clear one."""
        registry = self.ctx.registry
        assert registry is not None
        seen = list(task.seen_replies)
        for d in task.deliveries:
            if d.channel == "inapp":
                continue
            replies = await registry.call(
                f"channel.{d.channel}.poll",
                PollRequest(
                    channel=d.channel,
                    to=d.to,
                    thread_id=d.thread_id,
                    token=task.token,
                    since=d.sent_at,
                ),
                ChannelReplies,
            )
            for reply in replies.replies:
                if reply.message_id in seen:
                    continue
                seen.append(reply.message_id)
                if d.channel == "gmail" and d.to.casefold() not in reply.by.casefold():
                    continue  # only the person we asked may answer by email
                try:
                    await self._record_answer(task, reply.text, reply.by or d.to, d.channel)
                    out.answered.append(task.id)
                    return True
                except Problem:
                    task = task.model_copy(update={"seen_replies": seen})
                    await self.tasks.put(task.id, task)
                    await self.deliver(task, task.escalation_level, unclear=True)
        if seen != task.seen_replies:
            await self.tasks.put(task.id, task.model_copy(update={"seen_replies": seen}))
        return False
