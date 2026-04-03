# CLI + Flask Audit and Recovery Plan

## Executive Summary

ThreadBear currently has **two orchestration surfaces**:
- `flask_chat_app.py` (web app + API middle layer).
- `cli/app.py` (Textual terminal UI with direct service calls).

Both paths duplicate core chat orchestration logic (provider selection, compaction, tool loop, context injection, streaming handling). This duplication has drifted and is now the primary reason the CLI is unstable and incomplete.

The fastest path to a reliable CLI with full ThreadBear feature parity is:
1. Keep Flask as the canonical middle-layer implementation for business logic.
2. Refactor common app operations into a shared service layer used by both Flask routes and CLI commands.
3. Slim the CLI into a presentation layer that invokes shared service calls instead of owning independent orchestration logic.

---

## What Exists Today

### Flask app capabilities (source of truth)

`flask_chat_app.py` exposes a broad API surface including:
- Chat lifecycle + streaming/cancel APIs.
- Tool configuration and toolbox/toolbelt execution APIs.
- Folder graph, workspace, memory notes, prompt assignment, and edge management APIs.
- Context document ingestion/selection/highlights APIs.
- Prompt CRUD, endpoint CRUD, model catalogs/settings, llama.cpp URL/state APIs.

This is effectively a full middle-tier and state manager for ThreadBear features.

### CLI capabilities

`cli/app.py` contains:
- A Textual UI shell (sidebar/chat/settings panes).
- Direct invocation of low-level managers (`ChatManager`, `ConfigManager`, folder/doc/tool modules).
- A large command parser with many commands implemented as partial, local-only versions.

The CLI does implement some important behaviors (streaming, provider/model selection, docs, folders, tool toggles), but it does not cleanly consume the full feature model that the Flask app already provides.

---

## Audit Findings (Root Causes)

## 1) Architectural duplication and drift

The CLI has an independent chat-turn executor (`run_chat_turn`) that reimplements:
- system prompt composition,
- tool iteration loop,
- overflow compaction,
- usage/cost handling,
- title generation.

This same category of logic also exists in the Flask route flow, creating permanent drift risk.

**Impact:** fixes and new features must be shipped twice; parity failures are expected.

## 2) Feature-surface mismatch (Flask > CLI)

Flask includes rich APIs for folder memory notes, workspace/status transitions, prompt CRUD, document highlights/full doc retrieval, endpoint tests, browse catalog toggles, and more. The CLI command set only exposes a subset and often only basic list/create variants.

**Impact:** “CLI should have all ThreadBear features” is currently not achievable without major reconciliation.

## 3) Config-path inconsistencies

The CLI uses direct config key writes in command handlers (e.g., tool toggles) while other paths rely on structured accessors (`get_tool_config`, model settings helpers). This split increases probability of subtle behavior differences between UI surfaces.

**Impact:** settings can appear set in one surface but behave differently in another.

## 4) UI and business logic are tightly coupled in CLI

`MainScreen` command handlers mutate storage and domain state directly (chat files, folders, docs, toolbelt). There is no service boundary.

**Impact:** hard to test; fragile command behavior; difficult to add parity features without further complexity.

## 5) Missing contract-level parity tests

There is no automated parity check that compares CLI-visible operations against Flask-backed behavior.

**Impact:** regressions accumulate unnoticed until manual use.

---

## Recommended Target Architecture

### Principle: one domain core, two UIs

Create a shared application service layer (example package: `threadbear_services/`) with explicit use-cases:
- `ChatService` (new chat, send/stream, cancel, summarize, delete message, branch).
- `ProviderService` (providers, models, model settings, catalog refresh, endpoint integrations).
- `DocsService` (upload/url ingest/list/select/delete/highlights/full retrieval).
- `ToolingService` (tools config, toolbox CRUD, toolbelt CRUD/run/scan/permissions).
- `FolderService` (CRUD, assign chat/file, memory notes, prompts, workspace/status transitions, edges).
- `PromptService` (prompt CRUD and selection).
- `LlamaService` (status, URL management, model refresh).

Then:
- Flask routes become thin HTTP adapters calling services.
- CLI commands become thin command adapters calling those same services.

---

## Implementation Plan

## Phase 0 — Stabilization baseline (1-2 days)

1. Freeze current behavior with smoke tests:
   - CLI app boot test.
   - Flask app boot test.
   - One send/stream round-trip smoke per provider mock.
2. Add “feature parity checklist” document derived from Flask API inventory.
3. Add structured logging around CLI command failures.

**Deliverable:** reproducible baseline and failure visibility.

## Phase 1 — Extract shared orchestration (3-5 days)

1. Move chat-turn orchestration out of CLI into `ChatService`.
2. Move tool-loop + compaction + usage/cost logic into shared functions.
3. Wire Flask `send/stream` flow to `ChatService`.
4. Wire CLI `/send` flow to `ChatService`.

**Deliverable:** one implementation for the highest-risk logic.

## Phase 2 — Command/API parity bridge (4-7 days)

1. Map every Flask capability to one CLI command family (or explicit “not in CLI by design”).
2. Add missing CLI command handlers for:
   - prompt CRUD,
   - endpoint test/refresh/catalog browse controls,
   - folder memory/workspace/status/edges,
   - doc highlights/full-view ops,
   - toolbox/toolbelt parity operations.
3. Route all handlers through services (no direct file mutations in UI layer).

**Deliverable:** CLI can execute all supported ThreadBear features.

## Phase 3 — Reliability hardening (3-5 days)

1. Add contract tests for services (unit).
2. Add adapter tests:
   - Flask route → service call mapping.
   - CLI command → service call mapping.
3. Add parity regression test suite using the Phase 2 mapping matrix.

**Deliverable:** parity breaks fail CI before release.

## Phase 4 — UX simplification (2-4 days)

1. Simplify CLI command grammar and help output (grouped subcommands).
2. Standardize result rendering and error messaging.
3. Add guided command discovery (e.g., `/help docs`, `/help folders`).

**Deliverable:** streamlined CLI without sacrificing capability.

---

## Priority Backlog (Ordered)

1. **P0:** extract shared chat-turn engine from CLI.
2. **P0:** add parity matrix and missing-feature inventory.
3. **P1:** move doc/folder/tool/prompt/endpoint mutations behind services.
4. **P1:** implement missing CLI command families for Flask parity.
5. **P1:** add service + adapter tests.
6. **P2:** CLI UX cleanup and command discoverability improvements.

---

## Suggested Acceptance Criteria

A release is considered successful when all are true:

1. CLI startup and first message send succeed on clean repo state.
2. For every feature in the Flask parity matrix, a corresponding CLI command path passes.
3. Shared service unit tests and CLI/Flask adapter tests are green.
4. No direct persistence writes occur inside CLI view classes except via services.
5. New feature additions require only service changes plus thin adapter wiring.

---

## Immediate Next Step

Start with **Phase 1 extraction of chat-turn orchestration** from `cli/app.py` into a shared service module and update both Flask and CLI to call it. This removes the highest-risk duplication first and creates the seam needed for full feature parity work.
