"""Durable batch jobs (LangGraph). A job pauses where a person must decide, its state is saved
(checkpointer: memory / SQLite / Postgres), and it resumes on the answer, even after a restart.

replenishment_run:  plan every store → ask the vendor about unmatched demand (info.request)
                    → send orders within policy → pause for the buyer on each order over the
                    auto-order limit → send the approved ones → report to ops.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Any, TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from vl_core.checkpoints import open_checkpointer
from vl_core.errors import Problem
from vl_core.obs import get_logger
from vl_core.schemas import HumanTask, InfoRequest, JobRun, OrderProposal, ReplenishmentRunIn
from vl_core.store import get_repo

if TYPE_CHECKING:
    from retailer_svc.operations import RetailerOperations

log = get_logger("jobs")


class RunState(TypedDict, total=False):
    run_id: str
    retailer_id: str
    vendor_id: str
    for_date: str
    stores: list[str]
    planned: list[dict[str, Any]]
    unmatched: list[dict[str, Any]]
    approvals: dict[str, str]
    results: list[dict[str, Any]]


class JobRunner:
    def __init__(self, ops: RetailerOperations) -> None:
        self.ops = ops
        self.jobs = get_repo(JobRun, "retailer.jobs", ops.settings)
        self._stack = AsyncExitStack()
        self._graph: Any = None
        self._lock = asyncio.Lock()

    async def graph(self) -> Any:
        if self._graph is None:
            saver = await self._stack.enter_async_context(open_checkpointer(self.ops.settings))
            self._graph = self._build().compile(checkpointer=saver)
        return self._graph

    async def close(self) -> None:
        await self._stack.aclose()
        self._graph = None

    # ---- the graph -----------------------------------------------------------------------------

    def _build(self) -> StateGraph[RunState]:
        ops = self.ops

        async def plan(state: RunState) -> RunState:
            planned, unmatched = [], []
            active = await ops.active(state["retailer_id"])
            limit = f"{active.country_pack.currency} {active.retailer_policy.auto_order_max_value}"
            for store in state["stores"]:
                out = await ops.plan_for_store(
                    state["retailer_id"],
                    state["vendor_id"],
                    store,
                    date.fromisoformat(state["for_date"]),
                )
                gates = {g.order_id: g for g in out.gates}
                for p in out.plan.proposals:
                    gate = gates[p.order_id]
                    planned.append(
                        {
                            "store": store,
                            "proposal": p.model_dump(mode="json"),
                            "auto": gate.auto,
                            "reason": gate.reason,
                            "limit": limit,
                            "value": f"{p.currency} {p.total_value()}",
                        }
                    )
                unmatched += [{"store": store, **d.model_dump()} for d in out.plan.unmatched]
            log.info(
                "run planned",
                run=state["run_id"],
                orders=len(planned),
                within_policy=sum(1 for p in planned if p["auto"]),
                need_buyer=sum(1 for p in planned if not p["auto"]),
                no_listing=",".join(sorted({u["sku"] for u in unmatched})) or None,
            )
            return {"planned": planned, "unmatched": unmatched}

        async def ask_vendor(state: RunState) -> RunState:
            """Demand she has no listing for: ask her (in-app, her language) via the gateway."""
            for u in state.get("unmatched", []):
                request = InfoRequest(
                    field=f"supply:{u['sku']}",
                    ask="vendor",
                    answer_type="number",
                    question_key="hitl.q.supply",
                    params={
                        "sku": u["sku"],
                        "qty": u["qty"],
                        "unit": u["unit"],
                        "date": state["for_date"],
                    },
                )
                await ops.hitl.info_request(
                    state["retailer_id"],
                    state["vendor_id"],
                    request,
                    subject_ref=f"{state['run_id']}:{u['store']}",
                    request_id=f"{state['run_id']}-{u['store'].split(':')[-1]}-{u['sku']}",
                )
            return {}

        async def approve(state: RunState) -> RunState:
            """Pause once per order over the auto-order limit; the buyer's answer resumes here."""
            approvals = dict(state.get("approvals", {}))
            for item in state["planned"]:
                order_id = item["proposal"]["order_id"]
                if item["auto"] or order_id in approvals:
                    continue
                approvals[order_id] = interrupt(
                    {
                        "order_id": order_id,
                        "store": item["store"],
                        "value": item["value"],
                        "limit": item.get("limit", item["reason"]),
                    }
                )
            return {"approvals": approvals}

        async def send(state: RunState, *, auto: bool) -> RunState:
            results = list(state.get("results", []))
            done = {r["order_id"] for r in results}
            for item in state["planned"]:
                proposal = OrderProposal.model_validate(item["proposal"])
                if item["auto"] != auto or proposal.order_id in done:
                    continue
                ok = auto or state.get("approvals", {}).get(proposal.order_id) == "approve"
                order = await ops.create_order(
                    state["retailer_id"], proposal, send=ok, reason="" if ok else "buyer said no"
                )
                results.append(
                    {"order_id": order.order_id, "store": item["store"], "status": order.status}
                )
                log.info(
                    "run order " + ("sent" if ok else "held"),
                    run=state["run_id"],
                    order_id=order.order_id,
                    store=item["store"],
                    status=order.status,
                )
            return {"results": results}

        async def send_auto(state: RunState) -> RunState:
            """Orders within policy go now; they never wait behind someone else's decision."""
            return await send(state, auto=True)

        async def send_approved(state: RunState) -> RunState:
            return await send(state, auto=False)

        async def report(state: RunState) -> RunState:
            results = state.get("results", [])
            # waiting on Maya (her yes) or on the buyer (a counter outside policy)
            waiting = sum(
                1 for r in results if r["status"] in ("awaiting_vendor", "countered", "queued")
            )
            await ops.hitl.notify(
                state["retailer_id"],
                "ops",
                "hitl.q.run_done",
                {
                    "date": state["for_date"],
                    "sent": len(results) - waiting,
                    "waiting": waiting,
                    "skipped": len(state.get("unmatched", [])),
                },
                state["run_id"],
            )
            return {}

        graph = StateGraph(RunState)
        steps = [
            ("plan", plan),
            ("ask_vendor", ask_vendor),
            ("send_auto", send_auto),
            ("approve", approve),
            ("send_approved", send_approved),
            ("report", report),
        ]
        for name, fn in steps:
            graph.add_node(name, fn)
        graph.add_edge(START, steps[0][0])
        for (a, _), (b, _) in pairwise(steps):
            graph.add_edge(a, b)
        graph.add_edge(steps[-1][0], END)
        return graph

    # ---- running -------------------------------------------------------------------------------

    async def start(self, retailer_id: str, req: ReplenishmentRunIn) -> JobRun:
        retailer = await self.ops.store.retailers.get(retailer_id)
        stores = req.stores or [s.id for s in (retailer.stores if retailer else [])]
        for_date = req.for_date
        if for_date in ("today", "tomorrow"):  # the nightly scheduler sends "tomorrow"
            tz = ZoneInfo((await self.ops.active(retailer_id)).country_pack.timezone)
            local = datetime.now(tz).date()
            for_date = (local + timedelta(days=for_date == "tomorrow")).isoformat()
        now = datetime.now(UTC)
        run = JobRun(
            id=f"run-{uuid.uuid4().hex[:8]}",
            kind="replenishment_run",
            retailer_id=retailer_id,
            status="running",
            started_at=now,
            updated_at=now,
        )
        await self.jobs.put(run.id, run)
        log.info(
            "replenishment run started",
            run=run.id,
            vendor_id=req.vendor_id,
            for_date=for_date,
            stores=len(stores),
        )
        state: RunState = {
            "run_id": run.id,
            "retailer_id": retailer_id,
            "vendor_id": req.vendor_id,
            "for_date": for_date,
            "stores": stores,
        }
        return await self._advance(run, state)

    async def resume(self, job_id: str, task: HumanTask) -> JobRun:
        run = await self.jobs.get(job_id)
        if run is None:
            raise Problem(404, "unknown_job", job_id)
        value = task.answer_value if task.status == "answered" else "reject"
        log.info("run resumed", run=job_id, task=task.id, answer=value)
        return await self._advance(run, Command(resume=value))

    async def _advance(self, run: JobRun, payload: Any) -> JobRun:
        async with self._lock:
            graph = await self.graph()
            config = {"configurable": {"thread_id": run.id}}
            try:
                out = await graph.ainvoke(payload, config)
            except Exception as exc:
                failed = run.model_copy(
                    update={
                        "status": "failed",
                        "error": repr(exc)[:300],
                        "updated_at": datetime.now(UTC),
                    }
                )
                await self.jobs.put(run.id, failed)
                log.exception("run failed", run=run.id)
                raise
        waiting: list[str] = []
        for pending in out.get("__interrupt__", []):
            v = pending.value
            task = await self.ops.hitl.ask(
                run.retailer_id,
                InfoRequest(
                    field="approval",
                    ask="buyer",
                    kind="approval",
                    answer_type="choice",
                    question_key="hitl.q.order_over_limit",
                    choices=await self.ops.hitl.approve_choices(run.retailer_id),
                    params={
                        "order": v["order_id"],
                        "store": await self.ops.store_name(run.retailer_id, v["store"]),
                        "value": v["value"],
                        "limit": v["limit"],
                        "vendor": await self.ops.vendor_name(out.get("vendor_id", "")),
                    },
                ),
                subject_ref=v["order_id"],
                resume=f"job:{run.id}",
                task_id=f"{run.id}-{v['order_id']}",
            )
            waiting.append(task.id)
        results = out.get("results", [])
        updated = run.model_copy(
            update={
                "status": "waiting" if waiting else "done",
                "waiting_on": waiting,
                "updated_at": datetime.now(UTC),
                "summary": {
                    "planned": len(out.get("planned", [])),
                    "orders": results,
                    "asked_vendor": [u["sku"] for u in out.get("unmatched", [])],
                    "approvals": out.get("approvals", {}),
                },
            }
        )
        await self.jobs.put(run.id, updated)
        if waiting:
            log.info("run paused for the buyer", run=run.id, waiting_on=",".join(waiting))
        else:
            log.info("run done", run=run.id, orders=len(results))
        self.ops._publish("job", updated)
        return updated
