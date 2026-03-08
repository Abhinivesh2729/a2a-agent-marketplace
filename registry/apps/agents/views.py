import logging
import time
import uuid

import requests
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import Agent, TaskTrace

logger = logging.getLogger(__name__)


def _to_capability_list(capabilities):
    return [c.lower() for c in capabilities or []]


def _now_iso():
    return timezone.now().isoformat()


def _new_task_id():
    return f"task_{uuid.uuid4().hex[:12]}"


def _match_agents_by_capability(capability, exclude_names=None):
    exclude_names = exclude_names or set()
    matched = []
    for agent in Agent.objects.filter(status='active'):
        if agent.name in exclude_names:
            continue
        if capability.lower() in _to_capability_list(agent.capabilities):
            matched.append(agent)
    return matched


def _find_orchestrator_agent():
    for agent in Agent.objects.filter(status='active'):
        caps = _to_capability_list(agent.capabilities)
        if 'orchestration' in caps or 'planning' in caps or 'task_decomposition' in caps:
            return agent
    return None


def _upsert_trace(task_id, user_input, goal, selection_mode):
    return TaskTrace.objects.update_or_create(
        task_id=task_id,
        defaults={
            'user_input': user_input,
            'goal': goal or '',
            'selection_mode': selection_mode,
            'status': 'in_progress',
            'error': '',
        },
    )[0]


def _append_hop(trace, hop):
    hops = list(trace.hops or [])
    hops.append(hop)
    trace.hops = hops
    trace.save(update_fields=['hops', 'updated_at'])


def _complete_trace(trace, status, final_result=None, error=''):
    trace.status = status
    trace.final_result = final_result
    trace.error = error
    trace.completed_at = timezone.now()
    trace.save(update_fields=['status', 'final_result', 'error', 'completed_at', 'updated_at'])


def _agent_json(agent):
    return {
        'id': agent.id,
        'name': agent.name,
        'description': agent.description,
        'capabilities': agent.capabilities,
        'endpoint_url': agent.endpoint_url,
    }


@api_view(['POST'])
def register_agent(request):
    data = request.data
    required = ['name', 'description', 'capabilities', 'endpoint']
    missing = [f for f in required if f not in data]
    if missing:
        return Response({'error': f"Missing fields: {missing}"}, status=400)

    agent, created = Agent.objects.update_or_create(
        name=data['name'],
        defaults={
            'description': data['description'],
            'capabilities': data['capabilities'],
            'endpoint_url': data['endpoint'],
            'status': 'active',
        }
    )
    return Response({
        'id': agent.id,
        'name': agent.name,
        'status': agent.status,
        'registered': created,
    }, status=201 if created else 200)


@api_view(['GET'])
def list_agents(request):
    agents = Agent.objects.all().values(
        'id', 'name', 'description', 'capabilities', 'endpoint_url', 'status', 'registered_at'
    )
    return Response({'agents': list(agents)})


@api_view(['GET'])
def list_traces(request):
    traces = TaskTrace.objects.all().values(
        'task_id',
        'selection_mode',
        'status',
        'created_at',
        'completed_at',
        'user_input',
        'goal',
    )[:30]
    return Response({'traces': list(traces)})


@api_view(['GET'])
def get_trace(request, task_id):
    try:
        trace = TaskTrace.objects.get(task_id=task_id)
    except TaskTrace.DoesNotExist:
        return Response({'error': f'Trace not found for task_id: {task_id}'}, status=404)

    return Response({
        'task_id': trace.task_id,
        'selection_mode': trace.selection_mode,
        'status': trace.status,
        'user_input': trace.user_input,
        'goal': trace.goal,
        'hops': trace.hops,
        'final_result': trace.final_result,
        'error': trace.error,
        'created_at': trace.created_at,
        'updated_at': trace.updated_at,
        'completed_at': trace.completed_at,
    })


@api_view(['GET'])
def search_agents(request):
    capability = request.query_params.get('capability', '').lower()
    if not capability:
        return Response({'error': 'capability parameter required'}, status=400)

    agents = [
        a for a in Agent.objects.filter(status='active')
        if capability in [c.lower() for c in a.capabilities]
    ]
    result = [
        {'id': a.id, 'name': a.name, 'description': a.description,
         'capabilities': a.capabilities, 'endpoint_url': a.endpoint_url}
        for a in agents
    ]
    return Response({'agents': result})


def _dispatch_a2a(agent, payload, timeout=30):
    start = time.perf_counter()
    response = requests.post(f"{agent.endpoint_url}/execute", json=payload, timeout=timeout)
    duration_ms = int((time.perf_counter() - start) * 1000)
    return response, duration_ms


@api_view(['POST'])
def orchestrate(request):
    """Task entrypoint for specific-agent dispatch or auto orchestration."""
    task_input = (request.data.get('input') or request.data.get('user_task') or '').strip()
    goal = (request.data.get('goal') or '').strip()
    capability = (request.data.get('capability') or '').strip().lower()
    selection_mode = (request.data.get('selection_mode') or 'auto').strip().lower()
    selected_agent_name = (request.data.get('agent_name') or '').strip()
    task_id = (request.data.get('task_id') or '').strip() or _new_task_id()

    if not task_input:
        return Response({'error': 'input is required'}, status=400)

    if selection_mode not in {'auto', 'specific'}:
        return Response({'error': 'selection_mode must be auto or specific'}, status=400)

    with transaction.atomic():
        trace = _upsert_trace(task_id, task_input, goal, selection_mode)

    if selection_mode == 'specific':
        if not capability:
            return Response({'error': 'capability is required for specific mode'}, status=400)

        matched_agents = _match_agents_by_capability(capability)
        if selected_agent_name:
            matched_agents = [a for a in matched_agents if a.name == selected_agent_name]

        if not matched_agents:
            _complete_trace(
                trace,
                status='failed',
                error=f'No active agent found for capability: {capability}',
            )
            return Response({'error': f'No active agent found for capability: {capability}'}, status=404)

        agent = matched_agents[0]
        a2a_payload = {
            'task_id': task_id,
            'capability': capability,
            'input': task_input,
            'context': {
                'goal': goal,
                'selection_mode': 'specific',
            },
        }

        try:
            resp, duration_ms = _dispatch_a2a(agent, a2a_payload)
            resp_json = resp.json()
            hop = {
                'hop_num': 1,
                'timestamp': _now_iso(),
                'from': 'registry',
                'to': agent.name,
                'capability': capability,
                'request': a2a_payload,
                'response': resp_json,
                'http_status': resp.status_code,
                'duration_ms': duration_ms,
                'status': 'success' if resp.ok else 'error',
            }
            _append_hop(trace, hop)

            if not resp.ok:
                _complete_trace(trace, status='failed', error=str(resp_json))
                return Response({'task_id': task_id, 'error': 'Agent returned an error', 'trace_id': task_id}, status=502)

            _complete_trace(trace, status='success', final_result=resp_json)
            return Response({'task_id': task_id, 'agent': agent.name, 'result': resp_json, 'trace_id': task_id})
        except requests.RequestException as exc:
            logger.error(f"A2A call to {agent.name} failed: {exc}")
            hop = {
                'hop_num': 1,
                'timestamp': _now_iso(),
                'from': 'registry',
                'to': agent.name,
                'capability': capability,
                'request': a2a_payload,
                'response': {'error': str(exc)},
                'duration_ms': 0,
                'status': 'error',
            }
            _append_hop(trace, hop)
            _complete_trace(trace, status='failed', error=f'Agent call failed: {exc}')
            return Response({'task_id': task_id, 'error': f'Agent call failed: {exc}', 'trace_id': task_id}, status=502)

    orchestrator = _find_orchestrator_agent()
    if not orchestrator:
        _complete_trace(trace, status='failed', error='No active orchestrator agent found')
        return Response({'error': 'No active orchestrator agent found', 'task_id': task_id}, status=404)

    available_agents = [
        _agent_json(agent)
        for agent in Agent.objects.filter(status='active').exclude(id=orchestrator.id)
    ]
    orch_payload = {
        'task_id': task_id,
        'capability': 'orchestration',
        'input': task_input,
        'context': {
            'goal': goal,
            'requested_capability': capability,
            'available_agents': available_agents,
            'fallback_policy': 'try_alternate_same_capability_once',
            'a2a_version': '1.0',
        },
    }

    try:
        resp, duration_ms = _dispatch_a2a(orchestrator, orch_payload, timeout=180)
        resp_json = resp.json()
        root_hop = {
            'hop_num': 1,
            'timestamp': _now_iso(),
            'from': 'registry',
            'to': orchestrator.name,
            'capability': 'orchestration',
            'request': orch_payload,
            'response': {
                'status': resp_json.get('status'),
                'result': resp_json.get('result'),
                'error': resp_json.get('error'),
            },
            'http_status': resp.status_code,
            'duration_ms': duration_ms,
            'status': 'success' if resp.ok else 'error',
        }
        _append_hop(trace, root_hop)

        extra_hops = resp_json.get('hops', [])
        for offset, hop in enumerate(extra_hops, start=2):
            logged_hop = dict(hop)
            logged_hop['hop_num'] = offset
            if 'timestamp' not in logged_hop:
                logged_hop['timestamp'] = _now_iso()
            _append_hop(trace, logged_hop)

        if not resp.ok or resp_json.get('status') == 'error':
            err = resp_json.get('error') or 'Orchestration failed'
            _complete_trace(trace, status='failed', final_result=resp_json, error=err)
            return Response({'task_id': task_id, 'trace_id': task_id, 'error': err, 'result': resp_json}, status=502)

        _complete_trace(trace, status='success', final_result=resp_json)
        return Response({
            'task_id': task_id,
            'trace_id': task_id,
            'agent': orchestrator.name,
            'plan': resp_json.get('plan', []),
            'result': resp_json,
        })
    except requests.RequestException as exc:
        logger.error(f"A2A call to orchestrator failed: {exc}")
        hop = {
            'hop_num': 1,
            'timestamp': _now_iso(),
            'from': 'registry',
            'to': orchestrator.name,
            'capability': 'orchestration',
            'request': orch_payload,
            'response': {'error': str(exc)},
            'duration_ms': 0,
            'status': 'error',
        }
        _append_hop(trace, hop)
        _complete_trace(trace, status='failed', error=f'Orchestrator call failed: {exc}')
        return Response({'task_id': task_id, 'trace_id': task_id, 'error': f'Orchestrator call failed: {exc}'}, status=502)
