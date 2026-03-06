import requests
import logging
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import Agent

logger = logging.getLogger(__name__)


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


@api_view(['POST'])
def orchestrate(request):
    """Receive a user task, find a suitable agent, forward via A2A, return response."""
    task_input = request.data.get('input', '')
    capability = request.data.get('capability', 'math')

    agents = [
        a for a in Agent.objects.filter(status='active')
        if capability in [c.lower() for c in a.capabilities]
    ]
    if not agents:
        return Response({'error': f"No active agent found for capability: {capability}"}, status=404)

    agent = agents[0]
    a2a_payload = {
        'task_id': f"task_{request.data.get('task_id', 'auto')}",
        'capability': capability,
        'input': task_input,
        'context': {},
    }

    try:
        resp = requests.post(f"{agent.endpoint_url}/execute", json=a2a_payload, timeout=30)
        resp.raise_for_status()
        return Response({'agent': agent.name, 'result': resp.json()})
    except requests.RequestException as e:
        logger.error(f"A2A call to {agent.name} failed: {e}")
        return Response({'error': f"Agent call failed: {str(e)}"}, status=502)
