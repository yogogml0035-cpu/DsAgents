# DsAgents Agent Harness

## Core Goal

Build a Harness-grade agent runtime base that keeps `Session`, `Harness`, `Hands`, `Resources`, and `Tools` as stable module boundaries. Capabilities should not be hard-coded into one runner, container, model, or workflow.

## Harness Principles

This project builds a Harness, not a framework chase. DeepAgents is a pluggable `Brain` or sub-Harness, and local deterministic analyzers should be pluggable executors. The project owns Session, events, resources, tool routing, and runtime state.

Stabilize interfaces, not implementations:

- `Session` stores complete durable task facts as append-only events.
- `Harness` reads Session history, derives a context window, requests execution, and writes resulting events back.
- `Hands` expose model/tool execution traces and pass real errors through.
- `Resources` owns durable stores, checkpointers, and artifact paths.
- `Tools` expose callable capabilities without binding them to one runner.

Session is not the context window. Summaries or trimmed views may be appended as events, but they must not replace raw events as the source of truth.

Keep the Harness thin. Prefer real error propagation, auditable tool results, recoverable events, and simple tool registration. Do not add service layers, policy frameworks, workflow engines, containers, or broad security/config systems until a real caller needs them.

## First Milestone

Ship the smallest runnable DeepAgents demo:

- one MinerU parsing tool;
- one DeepAgents factory;
- one `CompositeBackend` configuration;
- one minimal session runner.

Do not add a service layer, container setup, auth system, policy framework, or workflow engine until a real caller needs it.

## Runtime Rules

- MinerU calls use `http://10.11.0.110:6006` through async task APIs: `POST /tasks`, poll `GET /tasks/{task_id}`, then fetch `GET /tasks/{task_id}/result`.
- MinerU request parameters `backend=hybrid-engine` and `effort=high` are fixed and not user-configurable in this milestone.
- DeepAgents filesystem default stays `StateBackend`.
- Durable history and memory routes use `StoreBackend` backed by local SQLite `.db` files.
- Large artifacts and large tool/model logs go to the filesystem under `data/artifacts/`.
- The built-in DeepAgents virtual filesystem is used; do not add another virtual filesystem wrapper.
- Middleware may log model-visible messages, tool calls, tool results, and final answers. Do not attempt to print or persist hidden chain-of-thought.

## Simplicity Constraint

Prefer deleting scope over adding knobs. Every new abstraction must protect one of the five module boundaries above or be removed.
