"""
Agent Execution Engine for ThreadBear

Runs an autonomous agent loop on a branch in a background thread.
Adapted from lmagent's run_agent() (agent_main.py:464-900+).
"""
import threading
import queue
import time
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pathlib import Path

from .loop_detector import LoopDetector
from .completion_detector import detect_completion
from .todo_manager import TodoManager
from .plan_manager import PlanManager
from .system_prompts import build_agent_system_prompt


class AgentExecutionEngine:
    """
    Runs an autonomous agent loop on a branch in a background thread.
    """

    def __init__(self, branch_id: str, branch_manager, tool_registry,
                 config, api_clients, artifact_manager=None):
        self.branch_id = branch_id
        self.branch_manager = branch_manager
        self.tool_registry = tool_registry
        self.config = config
        self.api_clients = api_clients
        self.artifact_manager = artifact_manager

        self.event_queue = queue.Queue()  # SSE endpoint reads from this
        self.running = False
        self.paused = False
        self.thread = None
        self.iteration = 0
        self.max_iterations = config.get('max_agent_iterations', 100)

        # Per-branch managers
        self.todo_manager = TodoManager(branch_id, branch_manager)
        self.plan_manager = PlanManager(branch_id, branch_manager)
        self.loop_detector = LoopDetector(
            max_repeats=config.get('loop_max_repeats', 3),
            max_errors=config.get('loop_max_errors', 3),
            max_empty=config.get('loop_max_empty', 5)
        )

        # Messages for this session
        self.messages = []

    def start(self, goal: str):
        """Start the agent loop in a background thread."""
        if self.running:
            raise ValueError("Agent already running on this branch")
        self.running = True
        self.thread = threading.Thread(
            target=self._run_loop, args=(goal,), daemon=True,
            name=f"agent-{self.branch_id[:8]}"
        )
        self.thread.start()

    def pause(self):
        """Pause the agent (loop checks this flag between iterations)."""
        self.paused = True
        self.emit('status', 'paused')

    def resume(self):
        """Resume a paused agent."""
        self.paused = False
        self.emit('status', 'running')

    def stop(self):
        """Stop the agent (loop exits on next iteration check)."""
        self.running = False
        self.emit('status', 'stopped')

    def emit(self, event_type: str, data: Any):
        """Push event to queue for SSE consumption."""
        self.event_queue.put({
            'type': event_type,
            'data': data,
            'iteration': self.iteration,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

    def get_event(self, timeout: float = None) -> Optional[Dict]:
        """Get next event from queue."""
        try:
            return self.event_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _run_loop(self, goal: str):
        """
        Main agent loop.

        Each iteration:
        1. Check pause/stop flags
        2. Check dependencies (for depends_on edges)
        3. Build messages (system prompt + history + todo/plan context + artifacts)
        4. Compact if approaching context limit
        5. Call LLM with tools
        6. Execute any tool calls
        7. Check for completion
        8. Check for loops
        9. Save state periodically
        """
        try:
            branch = self.branch_manager.db.get_branch(self.branch_id)
            if not branch:
                self.emit('error', 'Branch not found')
                return

            # Check dependencies before starting
            ready, reason = self._check_dependencies()
            if not ready:
                self.emit('status', 'waiting')
                self.emit('content', f'Dependency not met: {reason}')
                # Poll until ready or stopped
                while not ready and self.running:
                    time.sleep(5)
                    ready, reason = self._check_dependencies()
                if not self.running:
                    return
                self.emit('status', 'running')

            self.messages = self._build_initial_messages(goal, branch)
            self.emit('status', 'running')

            while self.running and self.iteration < self.max_iterations:
                # Pause check — block until resumed or stopped
                while self.paused and self.running:
                    time.sleep(0.5)
                if not self.running:
                    break

                self.iteration += 1
                self.emit('iteration', self.iteration)

                # Inject todo/plan context and artifacts into system messages
                context_messages = self._inject_context(self.messages)
                context_messages = self._inject_artifacts(context_messages)

                # Compact if needed
                context_window = self._get_context_window()
                context_messages = self._compact_if_needed(context_messages, context_window)

                # Call LLM with tools
                provider = self.config.get('provider', 'groq')
                tool_schemas = self._get_allowed_tools(branch)

                response = self._call_llm(context_messages, provider, tool_schemas)

                if response is None:
                    self.emit('error', 'LLM call failed')
                    break

                has_tool_calls = bool(response.get('tool_calls'))
                has_content = bool(response.get('content'))

                # Process tool calls
                if has_tool_calls:
                    for tc in response['tool_calls']:
                        name = tc.get('function', {}).get('name', '')
                        try:
                            args = json.loads(tc.get('function', {}).get('arguments', '{}'))
                        except json.JSONDecodeError:
                            args = {}

                        self.emit('tool_start', {'name': name, 'args': args})
                        result = self.tool_registry.execute_tool(name, args)
                        self.emit('tool_end', {'name': name, 'result': result})

                        # Add to message history
                        self.messages.append({
                            'role': 'assistant',
                            'content': response.get('content'),
                            'tool_calls': [tc]
                        })
                        self.messages.append({
                            'role': 'tool',
                            'tool_call_id': tc.get('id', ''),
                            'content': json.dumps(result)
                        })

                        # Update loop detector
                        self.loop_detector.record_tool_call(name, args, result)

                # Process content response
                if has_content:
                    if not has_tool_calls:
                        self.messages.append({
                            'role': 'assistant',
                            'content': response['content']
                        })
                    self.emit('content', response['content'])

                    # Check for completion
                    is_complete, reason = detect_completion(
                        response['content'], has_tool_calls
                    )
                    if is_complete:
                        self.emit('complete', {
                            'reason': reason,
                            'iterations': self.iteration
                        })
                        self.branch_manager.transition_status(self.branch_id, 'review')
                        break

                # Empty iteration (no tools, no content)
                if not has_tool_calls and not has_content:
                    self.loop_detector.record_empty_iteration()

                # Check for loops
                if self.loop_detector.is_looping():
                    reason = self.loop_detector.get_reason()
                    self.emit('loop_detected', reason)
                    # Inject warning into messages
                    self.messages.append({
                        'role': 'system',
                        'content': f'WARNING: Loop detected — {reason}. Try a different approach or declare TASK_COMPLETE.'
                    })
                    self.pause()

                # Save state periodically
                if self.iteration % 5 == 0:
                    self._save_state()

        except Exception as e:
            self.emit('error', str(e))
        finally:
            self._save_state()
            self.running = False
            self.emit('status', 'stopped')

    def _build_initial_messages(self, goal: str, branch: dict) -> list:
        """Build the initial message list with system prompt + goal."""
        system_prompt = build_agent_system_prompt(
            goal, branch,
            self.todo_manager.get_context(),
            self.plan_manager.get_context()
        )
        return [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': f'Execute this task: {goal}'}
        ]

    def _inject_context(self, messages: list) -> list:
        """Inject current todo/plan context into a copy of messages."""
        injected = list(messages)
        todo_ctx = self.todo_manager.get_context()
        plan_ctx = self.plan_manager.get_context()
        if todo_ctx or plan_ctx:
            context = '\n\n'.join(filter(None, [todo_ctx, plan_ctx]))
            # Update the system message with current state
            if injected and injected[0].get('role') == 'system':
                # Append to existing system message
                injected[0] = dict(injected[0])
                injected[0]['content'] += f'\n\n{context}'
            else:
                injected.insert(0, {'role': 'system', 'content': context})
        return injected

    def _get_context_window(self) -> int:
        """Get the context window size for the current model."""
        provider = self.config.get('provider', 'groq')
        model = self.config.get(f'{provider}_model', '')
        settings = self.config.get_model_settings(provider, model) if hasattr(self.config, 'get_model_settings') else {}
        return settings.get('context_window', 8000)

    def _compact_if_needed(self, messages: list, context_window: int) -> list:
        """Compact messages if approaching context limit."""
        # Simple token estimation
        total_tokens = sum(len(m.get('content', '')) // 4 for m in messages)
        threshold = int(context_window * 0.8)
        
        if total_tokens < threshold or len(messages) <= 10:
            return messages
        
        # Keep system message and last 10 messages
        if len(messages) > 11:
            compacted = [messages[0]] + messages[-10:]
            return compacted
        return messages

    def _get_allowed_tools(self, branch: dict) -> list:
        """Get tool schemas filtered by branch policy."""
        policy = json.loads(branch.get('policy', '{}')) if branch.get('policy') else {}
        allowed = policy.get('allowed_tools', None)  # None = all tools
        return self.tool_registry.get_schemas_for_provider(allowed)

    def _call_llm(self, messages: list, provider: str, tool_schemas: list):
        """
        Call the LLM and collect full response (content + tool_calls).
        """
        try:
            content_parts = []
            tool_calls = []

            # Get the stream function for this provider
            stream_func = {
                "groq": self.api_clients.call_groq_stream,
                "google": self.api_clients.call_google_stream,
                "mistral": self.api_clients.call_mistral_stream,
                "openrouter": self.api_clients.call_openrouter_stream,
                "llamacpp": self.api_clients.call_llamacpp_stream,
            }.get(provider)

            if not stream_func:
                self.emit('error', f'Unknown provider: {provider}')
                return None

            # Build config
            merged_cfg = dict(self.config.config)
            merged_cfg.update({
                'model': self.config.get(f'{provider}_model', ''),
                'temperature': self.config.get(f'{provider}_temperature', 0.7),
            })

            for chunk in stream_func(messages, merged_cfg, tools=tool_schemas):
                if isinstance(chunk, dict) and chunk.get('type') == 'tool_calls':
                    tool_calls.extend(chunk['tool_calls'])
                elif isinstance(chunk, str):
                    content_parts.append(chunk)

            return {
                'content': ''.join(content_parts) if content_parts else None,
                'tool_calls': tool_calls if tool_calls else None
            }
        except Exception as e:
            self.emit('error', f'LLM call error: {e}')
            return None

    def _save_state(self):
        """Persist agent state to branch metadata."""
        branch = self.branch_manager.db.get_branch(self.branch_id)
        if not branch:
            return

        meta = json.loads(branch.get('metadata', '{}')) if branch.get('metadata') else {}
        meta['agent_state'] = {
            'messages': self.messages[-100:],  # Keep last 100 messages for resume
            'iteration': self.iteration,
            'loop_detector': self.loop_detector.to_dict(),
            'last_saved': datetime.now(timezone.utc).isoformat()
        }
        self.branch_manager.db.upsert_branch(self.branch_id, metadata=meta)

    def _check_dependencies(self) -> tuple:
        """
        Check if all depends_on branches are complete (merged or archived).
        Returns (ready: bool, reason: str).
        """
        all_edges = self.branch_manager.db.get_edges(
            self.branch_id, direction='from'
        )
        deps = [e for e in all_edges if e.get('type') == 'depends_on']
        for dep in deps:
            target = self.branch_manager.db.get_branch(dep.get('to_branch'))
            if not target:
                continue
            if target.get('status') not in ('merged', 'archived'):
                return False, f"Waiting for '{target.get('title')}' to complete (currently {target.get('status')})"
        return True, ''

    def _inject_artifacts(self, messages: list) -> list:
        """
        Inject artifacts from connected branches into system messages.
        Artifacts are injected after the initial system message.
        """
        if not self.artifact_manager:
            return messages

        artifacts = self.artifact_manager.list_branch_artifacts(
            self.branch_id, include_incoming=True
        )
        incoming = artifacts.get('incoming', [])
        if not incoming:
            return messages

        context_parts = ["\n\n## AVAILABLE ARTIFACTS FROM OTHER BRANCHES:"]
        for a in incoming:
            name = a.get('name', a.get('id', 'Unknown'))
            atype = a.get('type', 'unknown')
            producer = a.get('producer_branch_id', 'unknown')[:8]
            context_parts.append(f"\n### [{atype}] {name} (artifact_id: {a['id']}, from: {producer})")

            try:
                content = Path(a['path']).read_text(encoding='utf-8')
                if len(content) > 2000:
                    context_parts.append(content[:2000])
                    context_parts.append("\n... (truncated — use read_artifact tool for full content)")
                else:
                    context_parts.append(content)
            except Exception:
                context_parts.append(f"(Could not read — use read_artifact tool with id: {a['id']})")

        injection = '\n'.join(context_parts)

        injected = list(messages)
        # Insert after the first system message
        if injected and injected[0].get('role') == 'system':
            injected.insert(1, {'role': 'system', 'content': injection})
        else:
            injected.insert(0, {'role': 'system', 'content': injection})

        return injected
