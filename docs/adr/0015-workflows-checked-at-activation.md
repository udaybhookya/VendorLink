# ADR 0015 — Workflows are checked against the loaded agents at activation

Status: accepted · 18 Oct 2026 · `vl_core/config/loader.py` (`check_workflows`),
`config/retailer_profiles/grocery_chain.json`, `tests/unit/test_config.py`,
`tests/unit/agents/test_registry.py`

## Problem
Config B could not place an order. Its workflow set (`default`) runs `place_order` with
`order.replenish`, but neither the grocery-chain retailer profile nor the packaged-food business
profile loaded the Replenishment Planner, the only agent that offers it. Nothing noticed:

- `resolve_active_config` checked only that every *named* specialist exists. It never checked
  that the specialists it loads can run the workflows it selects.
- The Orchestrator found the gap only when someone placed an order
  (`409 capability_not_loaded`), long after Studio had activated the config.
- The demo for config B never places an order, so no script caught it either.

## Decisions
1. **A config must be able to run its own workflows.** `resolve_active_config` calls
   `check_workflows`, which resolves every workflow step the way the Orchestrator will:
   - a required step needs exactly one loaded agent offering its capability;
   - an optional step may have none, but never more than one;
   - core and channel agents always count, and a specialist counts only if the config loads it.

   A failure is a `ConfigError` that names the workflow, the step and the agents that could fill
   it. Studio activation answers `422 bad_config`, and nothing is activated.
2. **The same check runs offline.** `ConfigLoader.validate_all` resolves every preset, so
   `make check`, `python -m agents validate` and CI fail on the same gap. The message names the
   preset file.
3. **Config B loads the Replenishment Planner** from the grocery-chain retailer profile.
   Ordering is the retailer's behaviour, so it belongs in the retailer profile (as it does for
   gifting), not in the vendor's business profile. No code branches on the config.
4. **Tests hold the contract on both sides.** Unit tests cover a missing required step, an
   ambiguous step and a missing optional step. A registry test checks that the Orchestrator
   resolves every workflow of every preset on retailer-svc, and that config B plans a
   replenishment order in IDR.

## Consequences
- Adding a workflow step, or removing a specialist from a profile, now fails at once, in tests
  and in Studio, instead of at the first order.
- Two specialists offering the same capability (for example two `grade.*` graders) can no
  longer be loaded together. That was already a run-time error in the Orchestrator; it is now
  reported when the config is activated.
- The check covers presets and every activation. A profile pairing that no preset uses is
  checked when someone activates it.
