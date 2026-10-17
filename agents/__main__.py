"""Agent command line.

    python -m agents list                              # every registered agent and its skills
    python -m agents validate                          # cards, code contracts, settings, hosts
    python -m agents run produce-grader --port 8101    # one agent as its own A2A service
    python -m agents invoke terms-agent terms.propose input.json   # one call, in-process
    python -m agents mcp-tools SLACK                   # what a configured MCP server offers
    python -m agents llm-check                         # one tiny call per configured model
    python -m agents new pricing-advisor --capability price.advise \\
        --input ListingStepIn --output ListingStepOut --llm  # scaffold + register
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agents.registry import AgentRegistry
from vl_core.config import ConfigLoader
from vl_core.settings import Settings, get_settings

PROMPTS_TEMPLATE = '''"""Prompts for {name}. One `Template` per prompt; fill with
`.substitute(...)` so a missing placeholder fails loudly instead of reaching the model
half-filled."""

from string import Template

MAIN = Template("""\\
Describe the task for the model here. Use $$placeholders for values filled at call time.
Return JSON only.
""")
'''

AGENT_TEMPLATE = '''"""{capability}: {description}"""

from __future__ import annotations

from agents.base import Agent, skill
from vl_core.schemas import {input}, {output}


class {cls}(Agent):
    @skill("{capability}")
    async def handle(self, inp: {input}) -> {output}:
        raise NotImplementedError("implement {agent_id}")
'''


def cmd_list(_: argparse.Namespace) -> int:
    registry = AgentRegistry.load()
    for r in sorted(registry.registered(), key=lambda r: (r.card.kind, r.card.agent_id)):
        card = r.card
        hosts = ",".join(r.entry.hosts) or "-"
        print(f"{card.agent_id:24} {card.kind:10} tenant={card.tenant:8} hosts={hosts}")
        for spec in card.skills:
            tag = " (internal)" if spec.internal else ""
            print(f"    {spec.capability:32} {spec.input_schema} → {spec.output_schema}{tag}")
    return 0


def cmd_validate(_: argparse.Namespace) -> int:
    problems = AgentRegistry.load().validate() + ConfigLoader().validate_all()
    for problem in problems:
        print(f"✗ {problem}")
    print("agents OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_run(args: argparse.Namespace) -> int:
    import uvicorn

    from agents.host import create_agent_app

    uvicorn.run(create_agent_app(args.agent_id), host=args.host, port=args.port)
    return 0


def cmd_invoke(args: argparse.Namespace) -> int:
    raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text()
    registry = AgentRegistry.load()
    registry.agent(args.agent_id)  # run in-process, whatever the config's route
    result = asyncio.run(registry.agent(args.agent_id).invoke(args.capability, json.loads(raw)))
    print(result.model_dump_json(indent=2))
    return 0


def cmd_mcp_tools(args: argparse.Namespace) -> int:
    """List what a configured MCP server offers, to match config/channels/<channel>.json."""
    from vl_core.mcp import McpClient, McpServerConfig

    config = McpServerConfig.from_env(args.server)
    if not config.configured:
        print(f"MCP_{args.server.upper()}_COMMAND / _URL is not set")
        return 1
    for tool in asyncio.run(McpClient(config).list_tools()):
        params = ", ".join((tool["input"] or {}).get("properties", {}))
        summary = next(iter((tool["description"] or "").splitlines()), "")[:80]
        print(f"{tool['name']}({params})  {summary}")
    return 0


def cmd_llm_check(_: argparse.Namespace) -> int:
    """Prove the model settings work: one small JSON call on FLASH_MODEL and on PRO_MODEL."""
    from pydantic import BaseModel

    from vl_core.llm import UnconfiguredLlm, get_llm

    class Ping(BaseModel):
        ok: bool
        word: str

    settings = get_settings()
    route = "Vertex AI" if settings.google_genai_use_vertexai else "GOOGLE_API_KEY"
    print(f"credentials: {route}")
    failed = 0
    for tier, model in (("flash", settings.flash_model), ("pro", settings.pro_model)):
        llm = get_llm(tier)  # type: ignore[arg-type]
        if isinstance(llm, UnconfiguredLlm):
            print(f"✗ {tier}: {tier.upper()}_MODEL is empty")
            failed += 1
            continue
        prompt = 'Return JSON {"ok": true, "word": "<the Telugu word for yes>"}.'
        result = asyncio.run(llm.generate_json(prompt, Ping))
        if result.value is not None and result.value.ok:
            print(f"✓ {tier}: {model} answered ({result.value.word})")
        else:
            print(f"✗ {tier}: {model}: {result.error}")
            failed += 1
    failed += _check_live_model(settings)
    return 1 if failed else 0


def _check_live_model(settings: Settings) -> int:
    """LIVE_MODEL must exist and support streaming (bidiGenerateContent). Returns 1 on failure."""
    live = settings.live_model
    if not live:
        print("✗ live: LIVE_MODEL is empty (voice off)")
        return 1
    from google import genai

    vertex = settings.google_genai_use_vertexai
    client = genai.Client(
        vertexai=vertex,
        project=(settings.google_cloud_project or None) if vertex else None,
        location=(settings.google_cloud_location or None) if vertex else None,
    )
    try:
        actions = client.models.get(model=live).supported_actions or []
    except Exception as exc:  # any lookup failure is reported to the operator
        print(f"✗ live: {live}: {type(exc).__name__}: {exc}")
        return 1
    if actions and "bidiGenerateContent" not in actions:
        print(f"✗ live: {live} exists but does not stream audio (pick a *-live* model)")
        return 1
    print(f"✓ live: {live} found")
    print("  (voice itself is checked in the browser: vendor app → Chat → hold the mic button)")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    folder_kind = "core" if args.kind == "core" else "specialists"
    folder = Path("agents") / folder_kind / args.agent_id.replace("-", "_")
    if folder.exists():
        print(f"{folder} already exists")
        return 1
    cls = "".join(part.title() for part in args.agent_id.split("-"))
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")
    card = {
        "agent_id": args.agent_id,
        "name": args.name or cls,
        "kind": args.kind,
        "tenant": args.tenant,
        "description": args.description,
        "version": "0.1.0",
        "skills": [
            {
                "capability": args.capability,
                "input_schema": args.input,
                "output_schema": args.output,
                "description": args.description,
            }
        ],
        "permissions": ["llm"] if args.llm else [],
        "transport": "local",
        "entrypoint": f"agents.{folder_kind}.{folder.name}.agent:{cls}",
    }
    (folder / "agent.json").write_text(json.dumps(card, indent=2) + "\n")
    (folder / "agent.py").write_text(
        AGENT_TEMPLATE.format(
            capability=args.capability,
            description=args.description,
            input=args.input,
            output=args.output,
            cls=cls,
            agent_id=args.agent_id,
        )
    )
    if args.llm:
        (folder / "prompts.py").write_text(PROMPTS_TEMPLATE.format(name=args.name or cls))
    config_path = get_settings().vl_config_dir / "agents.json"
    config = json.loads(config_path.read_text())
    host = {"vendor": "vendor-svc", "retailer": "retailer-svc"}.get(args.tenant)
    config["agents"].append(
        {
            "id": args.agent_id,
            "card": str(folder / "agent.json"),
            "enabled": False,
            "hosts": [host] if host else [],
        }
    )
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    print(f"created {folder}/ and registered {args.agent_id} (disabled) in {config_path}")
    print("next: implement agent.py, add tests, set enabled=true, list it in a profile")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m agents")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    run = sub.add_parser("run")
    run.add_argument("agent_id")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8100)
    run.set_defaults(fn=cmd_run)
    inv = sub.add_parser("invoke")
    inv.add_argument("agent_id")
    inv.add_argument("capability")
    inv.add_argument("input", help="JSON file, or - for stdin")
    inv.set_defaults(fn=cmd_invoke)
    mcp = sub.add_parser("mcp-tools", help="list tools of a configured MCP server (SLACK, GMAIL…)")
    mcp.add_argument("server")
    mcp.set_defaults(fn=cmd_mcp_tools)
    sub.add_parser("llm-check", help="one tiny call per configured model").set_defaults(
        fn=cmd_llm_check
    )
    new = sub.add_parser("new")
    new.add_argument("agent_id")
    new.add_argument("--capability", required=True)
    new.add_argument("--input", required=True, help="vl_core.schemas model name")
    new.add_argument("--output", required=True, help="vl_core.schemas model name")
    new.add_argument("--kind", choices=["specialist", "core"], default="specialist")
    new.add_argument("--tenant", choices=["vendor", "retailer", "any"], default="retailer")
    new.add_argument("--name", default="")
    new.add_argument("--description", default="")
    new.add_argument("--llm", action="store_true", help="also create prompts.py")
    new.set_defaults(fn=cmd_new)
    args = parser.parse_args()
    get_settings()  # also exports .env, which MCP servers and model SDKs read from the environment
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
