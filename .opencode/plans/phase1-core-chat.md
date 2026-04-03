# Phase 1: Core Chat - COMPLETED ✅

All features implemented in `cli/app.py` (1138 → 2072 lines).

## Implemented Features

### 1. Message Compaction & Overflow Handling ✅
- `compactor.should_compact()` called before each LLM call
- Overflow retry loop (up to 2 retries) with `force=True` compaction
- Error classification via `classify_error` and `ErrorClass.CONTEXT_OVERFLOW`

### 2. Chat Branching ✅
- `/branch [name]` — forks from last assistant message using `BranchManager.fork_branch()`
- `/branches` — lists all branches of current chat
- Creates side chat JSON with parent/root tracking

### 3. Chat Rename ✅
- `/rename <new_title>` — sanitizes title, preserves timestamp, updates folder mappings
- Auto-title generation after first exchange (uses `title_provider`/`title_model`)

### 4. Message Deletion ✅
- `/delete <index>` — delete by index
- `/delete last` — delete most recent message
- Recalculates tokens, saves with `force_save=True`

### 5. Temporary & Incognito Modes ✅
- `/temporary` — toggle (don't save chats to disk)
- `/incognito` — toggle (don't store user messages)
- State persisted in `self.temporary_mode` and `self.incognito_mode`

### 6. Tool Execution Loop ✅
- Full tool loop with `tool_registry` and `ToolSafetyManager`
- Tool schemas passed to stream function
- Tool call parsing, execution, result appending
- Max iterations with synthesis fallback
- Slim messages for tool loop iterations
- Tool events display in chat (`show_tool_start`, `show_tool_end`)
- Working text capture
- Tool result truncation with budget-based sizing

### 7. Context Documents ✅
- `/docs list` — list documents with selection status
- `/docs upload <path>` — upload file
- `/docs delete <name>` — delete document
- `/docs select <name>` / `/docs deselect <name>` — include/exclude from context
- `/docs url <url>` — ingest web page as document
- Documents always injected into `api_messages`

### 8. Token Context Display ✅
- `/context` — shows chat tokens + doc tokens vs context window

### 9. Folder Management ✅
- `/folders list` — tree view
- `/folders create <name> [parent]` — create folder
- `/folders contents <id>` — show chats in folder
- `/folders assign <id> <chat>` — assign chat to folder

### 10. Tool Configuration ✅
- `/tools enable <provider>` / `/tools disable <provider>`
- `/tools os <windows|linux|macos>` — set OS hint
- `/tools status` — show current tool config

### 11. Toolbox Management ✅
- `/toolbox list` — list all scripts (default + custom)
- `/toolbox view <file>` — read file contents

### 12. Toolbelt (Per-Chat Scripts) ✅
- `/toolbelt list` — show assigned scripts
- `/toolbelt add <script>` — assign script to chat
- `/toolbelt remove <script>` — remove from chat

### 13. Custom Endpoints ✅
- `/endpoints list` — list endpoints
- `/endpoints add <name> <base_url>` — add endpoint
- `/endpoints delete <id>` — delete endpoint

### 14. System Prompts ✅
- `/prompts list` — list all prompts (defaults + custom)

### 15. Cancel Generation ✅
- `/cancel` — sets `_cancel_event` to stop streaming
- Existing `_cancel_event` properly wired into tool loop

### 16. Cost Tracking ✅
- Token usage and cost displayed after each response
- Stored on message with `usage` and `cost` fields

## New Commands Summary
`/rename`, `/delete`, `/branch`, `/branches`, `/temporary`, `/incognito`, `/cancel`, `/context`, `/docs`, `/folders`, `/tools`, `/toolbox`, `/toolbelt`, `/endpoints`, `/prompts`

## New Methods Added
- `ThreadBearApp._maybe_generate_title()` — auto-title after first exchange
- `ThreadBearApp._rename_chat_file()` — rename chat with sanitization
- `ThreadBearApp._truncate_tool_result()` — smart truncate for tool results
- `ThreadBearApp._append_status()` — show status messages
- `ThreadBearApp._append_tool_event()` — show tool start
- `ThreadBearApp._append_tool_result()` — show tool end
- `ChatDisplay.show_status()` — status display
- `ChatDisplay.show_tool_start()` — tool start display
- `ChatDisplay.show_tool_end()` — tool end display

## Next Steps
- Phase 2: Polish and edge cases (per-model settings CRUD, model browse, llama.cpp management, prompt CRUD)
- Phase 3: Workspace lifecycle, memory notes, edges
