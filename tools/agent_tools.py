"""
Agent Tools for ThreadBear

Todo and plan management tools for agent execution.
"""
from .registry import tool_registry

# These will be set when tools are registered
_todo_manager = None
_plan_manager = None


def set_managers(todo_mgr, plan_mgr):
    """Set the managers for tool access."""
    global _todo_manager, _plan_manager
    _todo_manager = todo_mgr
    _plan_manager = plan_mgr


def tool_todo_add(args: dict) -> dict:
    """Add a todo item to the branch's todo list."""
    if not _todo_manager:
        return {'error': 'Todo manager not initialized'}
    description = args.get('description', '')
    notes = args.get('notes', '')
    if not description:
        return {'error': 'Description is required'}
    return _todo_manager.add(description, notes)


def tool_todo_complete(args: dict) -> dict:
    """Mark a todo item as completed."""
    if not _todo_manager:
        return {'error': 'Todo manager not initialized'}
    todo_id = args.get('todo_id')
    if not todo_id:
        return {'error': 'todo_id is required'}
    result = _todo_manager.complete(int(todo_id))
    return result if result else {'error': f'Todo #{todo_id} not found'}


def tool_todo_list(args: dict) -> dict:
    """List all todo items."""
    if not _todo_manager:
        return {'error': 'Todo manager not initialized'}
    return _todo_manager.list_all()


def tool_plan_create(args: dict) -> dict:
    """Create a plan with steps."""
    if not _plan_manager:
        return {'error': 'Plan manager not initialized'}
    title = args.get('title', 'Untitled Plan')
    steps = args.get('steps', [])
    if not steps:
        return {'error': 'At least one step is required'}
    return _plan_manager.create(title, steps)


def tool_plan_complete_step(args: dict) -> dict:
    """Mark a plan step as completed."""
    if not _plan_manager:
        return {'error': 'Plan manager not initialized'}
    step_id = args.get('step_id', '')
    notes = args.get('notes', '')
    return _plan_manager.complete_step(step_id, notes)


def tool_plan_get_next(args: dict) -> dict:
    """Get the next pending step in the plan."""
    if not _plan_manager:
        return {'error': 'Plan manager not initialized'}
    next_step = _plan_manager.get_next_step()
    return {'next_step': next_step} if next_step else {'next_step': None}


# Register the tools
tool_registry.register_tool('todo_add', tool_todo_add, {
    'description': 'Add a todo item to track progress on a multi-step task.',
    'properties': {
        'description': {'type': 'string', 'description': 'The todo description'},
        'notes': {'type': 'string', 'description': 'Optional notes'}
    },
    'required': ['description']
})

tool_registry.register_tool('todo_complete', tool_todo_complete, {
    'description': 'Mark a todo item as completed.',
    'properties': {
        'todo_id': {'type': 'integer', 'description': 'The todo ID to complete'}
    },
    'required': ['todo_id']
})

tool_registry.register_tool('todo_list', tool_todo_list, {
    'description': 'List all todo items with their status.',
    'properties': {},
    'required': []
})

tool_registry.register_tool('plan_create', tool_plan_create, {
    'description': 'Create a plan with ordered steps for complex tasks.',
    'properties': {
        'title': {'type': 'string', 'description': 'Plan title'},
        'steps': {
            'type': 'array',
            'description': 'List of step objects with description and optional dependencies',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'description': {'type': 'string'},
                    'dependencies': {'type': 'array', 'items': {'type': 'string'}}
                }
            }
        }
    },
    'required': ['title', 'steps']
})

tool_registry.register_tool('plan_complete_step', tool_plan_complete_step, {
    'description': 'Mark a plan step as completed.',
    'properties': {
        'step_id': {'type': 'string', 'description': 'The step ID to complete'},
        'notes': {'type': 'string', 'description': 'Optional completion notes'}
    },
    'required': ['step_id']
})

tool_registry.register_tool('plan_get_next', tool_plan_get_next, {
    'description': 'Get the next pending step in the plan (dependencies met).',
    'properties': {},
    'required': []
})
