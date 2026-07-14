---
date: 2026-07-11
type: pattern
tags: [test, orchestration, async, stub, pytest, redis]
project: event-harness
confidence: high
sources: []
related: ["[[test-isolation]]", "[[contract-driven-testing]]"]
provenance: inferred
session_id: a9779831ed4598deb
---

# Async Orchestration Test Stub Pattern

## Summary
When testing async orchestration code that spawns sub-agents or worker loops, use a keyword-callable stub factory that mirrors the real class signature while tracking concurrency and session IDs. Read the source first to verify the real contract (status strings, result dict keys) rather than making assumptions—adapt assertions to what the code actually does.

## Key Learnings

- **Stub factory pattern for loop delegation**: Create a `_StubLoopFactory` class with `__call__(self, *, client, config, llm_provider, session_id, tool_registry=None)` that records `session_id` in `self.sessions`, tracks `self.active`/`self.max_active` concurrency in try/finally, and returns an object with `async def run(...)` that sleeps, optionally raises on a substring match, else returns a structured result.

- **Monkeypatch at import path**: Use `monkeypatch.setattr("event_harness.agents.orchestrator.AgentLoop", stub)` to inject the stub before the orchestrator imports the real class—critical because the orchestrator does `AgentLoop(...)` inline.

- **Contract verification before writing tests**: Read the source file for the real status strings (`running` → `completed`/`error`/`timeout`/`cancelled`), result dict keys (`index`, `status`, plus `error` or `result`/`task_id`), and any internal key formats (fan-in: `eh:fan-in:{parent}:{batch}`). Adapt assertions to match—don't assume "failed" when the code says "error".

- **Concurrency validation**: Track peak concurrent executions in the stub via try/finally (increment on entry, decrement on exit) and assert `stub.max_active <= expected_limit` after parallel delegation runs.

- **Fan-in verification**: After `delegate_parallel`, scan Redis keys with `fake_redis.scan_iter(match=f"{PREFIX}:fan-in:{parent}:*")` to verify exactly one batch key exists, then read it back via the orchestrator's `get_fan_in_state` method to confirm result aggregation.

## Context
Discovered while adding QA-115 test coverage for Event Harness's `AgentOrchestrator.delegate_task` and `delegate_parallel` methods. The orchestrator spawns `AgentLoop` instances for sub-sessions; the stub factory enables testing orchestration logic (concurrency limits, error aggregation, state management) without running real LLM calls. The pattern generalizes to any async orchestration that delegates to spawned workers.
