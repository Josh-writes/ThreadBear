"""
Artifact Tools for ThreadBear

Tools for agents to create, list, read, and send artifacts.
Also includes delegate_subtask for creating work-order sub-branches.
"""
from tools.registry import tool_registry

# These will be set when tools are registered
_artifact_manager = None
_branch_manager = None
_start_agent_func = None


def set_managers(artifact_mgr, branch_mgr, start_agent_func=None):
    """Set the managers for tool access."""
    global _artifact_manager, _branch_manager, _start_agent_func
    _artifact_manager = artifact_mgr
    _branch_manager = branch_mgr
    _start_agent_func = start_agent_func


def create_artifact(args: dict) -> dict:
    """Agent declares output as a named artifact."""
    if not _artifact_manager:
        return {'error': 'Artifact manager not initialized'}
    
    try:
        return _artifact_manager.create_artifact(
            branch_id=args.get('branch_id'),
            artifact_type=args.get('type'),
            content=args.get('content'),
            name=args.get('name'),
            tags=args.get('tags', [])
        )
    except Exception as e:
        return {'error': str(e)}


tool_registry.register_tool('create_artifact', create_artifact, {
    'description': 'Save output as a named artifact that can be shared with other branches.',
    'properties': {
        'branch_id': {'type': 'string', 'description': 'Current branch ID'},
        'type': {'type': 'string', 'enum': ['document', 'code', 'image', 'data', 'summary'],
                 'description': 'Artifact type'},
        'content': {'type': 'string', 'description': 'Artifact content'},
        'name': {'type': 'string', 'description': 'Filename for the artifact'},
        'tags': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Tags for search'}
    },
    'required': ['branch_id', 'type', 'content']
})


def list_available_artifacts(args: dict) -> dict:
    """Agent sees artifacts from its branch + connected branches."""
    if not _artifact_manager:
        return {'error': 'Artifact manager not initialized', 'produced': [], 'incoming': []}
    
    return _artifact_manager.list_branch_artifacts(
        args.get('branch_id'), include_incoming=True
    )


tool_registry.register_tool('list_available_artifacts', list_available_artifacts, {
    'description': 'List artifacts produced by this branch and received from connected branches.',
    'properties': {
        'branch_id': {'type': 'string', 'description': 'Current branch ID'}
    },
    'required': ['branch_id']
})


def read_artifact(args: dict) -> dict:
    """Agent reads full content of a specific artifact."""
    if not _artifact_manager:
        return {'error': 'Artifact manager not initialized'}
    
    result = _artifact_manager.get_artifact(args.get('artifact_id'))
    return result if result else {'error': f'Artifact {args.get("artifact_id")} not found'}


tool_registry.register_tool('read_artifact', read_artifact, {
    'description': 'Read the full content of an artifact by its ID.',
    'properties': {
        'artifact_id': {'type': 'string', 'description': 'Artifact ID to read'}
    },
    'required': ['artifact_id']
})


def send_artifact(args: dict) -> dict:
    """Agent flows an artifact to another branch."""
    if not _artifact_manager:
        return {'error': 'Artifact manager not initialized'}
    
    try:
        success = _artifact_manager.flow_artifact(
            args.get('artifact_id'),
            args.get('from_branch_id'),
            args.get('to_branch_id')
        )
        return {'success': success}
    except Exception as e:
        return {'error': str(e)}


tool_registry.register_tool('send_artifact', send_artifact, {
    'description': 'Send an artifact to another branch, creating an artifact_flow edge.',
    'properties': {
        'artifact_id': {'type': 'string', 'description': 'Artifact to send'},
        'from_branch_id': {'type': 'string', 'description': 'Source branch'},
        'to_branch_id': {'type': 'string', 'description': 'Destination branch'}
    },
    'required': ['artifact_id', 'from_branch_id', 'to_branch_id']
})


def delegate_subtask(args: dict) -> dict:
    """
    Create a work-order branch under a parent and optionally start its agent.
    Inspired by lmagent's tool_task (sub-agent delegation).
    """
    if not _branch_manager:
        return {'error': 'Branch manager not initialized'}
    
    parent_id = args.get('parent_branch_id')
    goal = args.get('goal', '')
    name = args.get('name', f"Subtask: {goal[:40]}") if goal else 'Subtask'
    auto_start = args.get('auto_start', False)
    
    if not parent_id or not goal:
        return {'error': 'parent_branch_id and goal are required'}
    
    try:
        # Create work order
        branch = _branch_manager.create_work_order(parent_id, name, goal)
        
        # Optionally start agent on the new branch
        agent_started = False
        if auto_start and _start_agent_func:
            try:
                _start_agent_func(branch['id'], goal)
                agent_started = True
            except Exception as e:
                # Agent start failed, but branch was created
                pass
        
        return {
            'branch_id': branch['id'],
            'name': name,
            'status': 'created',
            'agent_started': agent_started
        }
    except Exception as e:
        return {'error': str(e)}


tool_registry.register_tool('delegate_subtask', delegate_subtask, {
    'description': 'Create a work-order sub-branch and optionally start an agent on it.',
    'properties': {
        'parent_branch_id': {'type': 'string', 'description': 'Parent branch ID'},
        'goal': {'type': 'string', 'description': 'Goal for the sub-task'},
        'name': {'type': 'string', 'description': 'Name for the new branch'},
        'auto_start': {'type': 'boolean', 'description': 'Start agent immediately (default false)'}
    },
    'required': ['parent_branch_id', 'goal']
}, destructive=True)
