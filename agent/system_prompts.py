"""
Agent System Prompts for ThreadBear

Builds system prompts for agent execution.
Adapted from lmagent agent_tools.py:1313-1369.
"""
import json


def build_agent_system_prompt(goal: str, branch: dict,
                               todo_context: str = '',
                               plan_context: str = '') -> str:
    """
    Build the system prompt for an agent execution.
    
    Establishes execution rules, branch context, and todo/plan state.
    """
    branch_meta = json.loads(branch.get('metadata', '{}')) if branch.get('metadata') else {}

    prompt = f"""You are an autonomous agent working on a specific task within ThreadBear.

## YOUR TASK
{goal}

## EXECUTION RULES
1. Every response must either use a tool OR declare TASK_COMPLETE.
2. Think step-by-step: orient (understand current state) → plan (decide next action) → execute (use tool) → verify (check result) → complete (when done).
3. Use the todo system to track your progress on multi-step tasks.
4. If you are stuck or need human input, explain what you need and stop calling tools.
5. Do NOT loop — if a tool call fails twice with the same error, try a different approach.
6. When the task is fully done, include TASK_COMPLETE in your response with a summary of what was accomplished.
7. Be concise in your reasoning. Focus on actions, not explanations.

## BRANCH CONTEXT
- Branch: {branch.get('name', 'Unknown')} ({branch.get('type', 'chat')})
- Status: {branch.get('status', 'active')}
"""

    if branch_meta.get('goal'):
        prompt += f"- Goal: {branch_meta['goal']}\n"

    if branch_meta.get('description'):
        prompt += f"- Description: {branch_meta['description']}\n"

    if todo_context:
        prompt += f"\n{todo_context}\n"

    if plan_context:
        prompt += f"\n{plan_context}\n"

    return prompt
