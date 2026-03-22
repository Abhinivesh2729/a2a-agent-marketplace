import json
import logging
import os
import re
import time
import uuid
from copy import deepcopy

import ollama
import requests
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

REGISTRY_URL = os.getenv('REGISTRY_URL', 'http://localhost:8000')
ORCHESTRATOR_PORT = int(os.getenv('ORCHESTRATOR_PORT', '8005'))
ORCHESTRATOR_MODEL = os.getenv('ORCHESTRATOR_MODEL', 'qwen2.5:3b')
MAX_HOPS = int(os.getenv('ORCHESTRATOR_MAX_HOPS', '5'))

AGENT_INFO = {
    'name': 'Task Orchestrator',
    'description': 'Plans and orchestrates multi-agent execution through A2A calls',
    'capabilities': ['orchestration', 'planning', 'task_decomposition'],
    'endpoint': f'http://localhost:{ORCHESTRATOR_PORT}',
}


def _lower_caps(agent):
    return [c.lower() for c in (agent.get('capabilities') or [])]


def _find_candidates(agents, capability):
    return [a for a in agents if capability.lower() in _lower_caps(a)]


def _supported_capabilities(agents):
    supported = set()
    for agent in agents:
        supported.update(_lower_caps(agent))
    return supported


def _resolve_capability(raw_capability, supported):
    cap = (raw_capability or '').strip().lower()
    if not cap:
        return ''
    if cap in supported:
        return cap

    alias_order = {
        'email': ['summarization', 'text', 'nlp'],
        'mail': ['summarization', 'text', 'nlp'],
        'summary': ['summarization', 'text', 'nlp'],
        'summarize': ['summarization', 'text', 'nlp'],
        'summarise': ['summarization', 'text', 'nlp'],
        'summerize': ['summarization', 'text', 'nlp'],
        'calc': ['math', 'calculator', 'arithmetic'],
        'calculation': ['math', 'calculator', 'arithmetic'],
        'orchestration': ['search', 'lookup', 'summarization', 'text', 'math'],
        'planning': ['search', 'lookup', 'summarization', 'text', 'math'],
        'task_decomposition': ['search', 'lookup', 'summarization', 'text', 'math'],
    }

    candidates = alias_order.get(cap, [])
    for candidate in candidates:
        if candidate in supported:
            return candidate
    return ''


def _extract_json(text):
    text = (text or '').strip()
    if text.startswith('{') and text.endswith('}'):
        return text

    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        return match.group(0)
    return ''


def _task_needs_math(text):
    lower = (text or '').lower()
    return bool(re.search(r'\d', lower)) and any(
        token in lower for token in ['calculate', 'sum', 'subtract', 'multiply', 'divide', 'power', '^', '+', '-', '*', '/']
    )


def _task_needs_summary(text, goal):
    combined = f"{text or ''} {goal or ''}".lower()
    summary_tokens = [
        'summarize',
        'summerize',
        'summarise',
        'summary',
        'explain',
        'brief',
        'email',
        'mail',
        'write up',
    ]
    return any(token in combined for token in summary_tokens)


def _task_needs_search(text, goal):
    combined = f"{text or ''} {goal or ''}".lower()
    search_tokens = [
        'search',
        'web',
        'lookup',
        'look up',
        'find',
        'google',
        'formula',
    ]
    return any(token in combined for token in search_tokens)


def _parse_requested_lines(context):
    lower = (context or '').lower()
    numeric = re.search(r'\b(\d{1,2})\s*lines?\b', lower)
    if numeric:
        return max(1, min(12, int(numeric.group(1))))

    word_map = {
        'one': 1,
        'two': 2,
        'three': 3,
        'four': 4,
        'five': 5,
        'six': 6,
    }
    for word, value in word_map.items():
        if re.search(rf'\b{word}\s+lines?\b', lower):
            return value
    return None


def _is_summary_capability(capability):
    return (capability or '').lower() in {'summarization', 'text', 'nlp'}


def _summary_instruction(goal_or_context):
    context = (goal_or_context or '').lower()
    lines = _parse_requested_lines(context)

    if 'json' in context:
        return (
            'Summarize this result in JSON format with keys "summary", "key_points", and "result" when relevant. '
            'After the JSON, add a short plain-English explanation. Input: {{previous_result}}'
        )
    if 'email' in context or 'mail format' in context:
        return 'Write a concise email-style summary for this result: {{previous_result}}'
    if 'bullet' in context or 'points' in context:
        return 'Summarize this result as concise bullet points: {{previous_result}}'
    if lines:
        return f'Summarize this result in about {lines} lines (best effort): {{{{previous_result}}}}'
    return 'Summarize this result in 1-2 sentences: {{previous_result}}'


def _post_process_plan(plan, task_input, goal, requested_capability, agents):
    """Ensure practical orchestration behavior for mixed-intent user tasks."""
    supported = _supported_capabilities(agents)
    processed = []
    for idx, step in enumerate(plan[:MAX_HOPS], start=1):
        capability = _resolve_capability(step.get('capability'), supported)
        if not capability:
            continue
        processed.append(
            {
                'step': idx,
                'capability': capability,
                'instruction': (step.get('instruction') or task_input).strip(),
                'preferred_agent': (step.get('preferred_agent') or '').strip(),
            }
        )

    needs_math = _task_needs_math(task_input)
    needs_summary = _task_needs_summary(task_input, goal)
    has_math = any(step['capability'] == 'math' for step in processed)
    has_summary = any(_is_summary_capability(step['capability']) for step in processed)
    needs_search = _task_needs_search(task_input, goal)
    has_search = any(step['capability'] in ('search', 'web_search', 'lookup') for step in processed)

    # Guarantee two-hop orchestration for tasks that require both compute + summarization.
    if needs_math and not has_math and _resolve_capability('math', supported):
        processed.insert(
            0,
            {
                'step': 1,
                'capability': _resolve_capability('math', supported),
                'instruction': task_input,
                'preferred_agent': '',
            },
        )

    # Add search step if task requires looking up information first.
    if needs_search and not has_search:
        search_cap = _resolve_capability('search', supported)
        if search_cap:
            processed.insert(0, {
                'step': 1,
                'capability': search_cap,
                'instruction': task_input,
                'preferred_agent': '',
            })

    summary_cap = _resolve_capability('summarization', supported) or _resolve_capability('text', supported)
    summary_context = f"{task_input} {goal}".strip()

    # Normalize summary instructions so downstream agent receives a clear format request.
    if needs_summary:
        for step in processed:
            if _is_summary_capability(step['capability']):
                step['instruction'] = _summary_instruction(summary_context)

    if needs_summary and not has_summary and summary_cap:
        processed.append(
            {
                'step': len(processed) + 1,
                'capability': summary_cap,
                'instruction': _summary_instruction(summary_context),
                'preferred_agent': '',
            }
        )

    if not processed:
        fallback = _fallback_plan(task_input, requested_capability)
        for i, step in enumerate(fallback, start=1):
            resolved = _resolve_capability(step.get('capability'), supported)
            if not resolved:
                continue
            processed.append(
                {
                    'step': i,
                    'capability': resolved,
                    'instruction': step.get('instruction', task_input),
                    'preferred_agent': step.get('preferred_agent', ''),
                }
            )

    # Keep execution practical: enforce minimal required chain for common mixed-intent tasks.
    final_sequence = processed
    if needs_search and needs_math:
        search_cap = _resolve_capability('search', supported)
        math_cap = _resolve_capability('math', supported)
        desired_order = [cap for cap in [search_cap, math_cap] if cap]
        if needs_summary and summary_cap:
            desired_order.append(summary_cap)
        minimal = []
        for desired_cap in desired_order:
            picked = next((step for step in processed if step['capability'] == desired_cap), None)
            if not picked:
                picked = {
                    'step': len(minimal) + 1,
                    'capability': desired_cap,
                    'instruction': task_input,
                    'preferred_agent': '',
                }
            if _is_summary_capability(desired_cap) and picked:
                picked = dict(picked)
                picked['instruction'] = _summary_instruction(summary_context)
            if picked:
                minimal.append(dict(picked))
        if minimal:
            final_sequence = minimal
    elif needs_math and needs_summary:
        summary_cap = _resolve_capability('summarization', supported) or _resolve_capability('text', supported)
        desired_order = [_resolve_capability('math', supported), summary_cap]
        minimal = []
        for desired_cap in desired_order:
            if not desired_cap:
                continue
            picked = next((step for step in processed if step['capability'] == desired_cap), None)
            if not picked and desired_cap == summary_cap:
                picked = {
                    'step': len(minimal) + 1,
                    'capability': desired_cap,
                    'instruction': _summary_instruction(f"{task_input} {goal}"),
                    'preferred_agent': '',
                }
            elif picked and _is_summary_capability(desired_cap):
                picked = dict(picked)
                picked['instruction'] = _summary_instruction(summary_context)
            if picked:
                minimal.append(dict(picked))
        if minimal:
            final_sequence = minimal
    elif needs_math and not needs_summary:
        math_cap = _resolve_capability('math', supported)
        picked = next((step for step in processed if step['capability'] == math_cap), None)
        if picked:
            final_sequence = [dict(picked)]
    elif needs_summary and not needs_math:
        summary_cap = _resolve_capability('summarization', supported) or _resolve_capability('text', supported)
        picked = next((step for step in processed if step['capability'] == summary_cap), None)
        if picked:
            final_sequence = [dict(picked)]

    # Reindex and bound by max hops.
    final_plan = []
    for i, step in enumerate(final_sequence[:MAX_HOPS], start=1):
        step['step'] = i
        final_plan.append(step)
    return final_plan


def _fallback_plan(task_input, requested_capability):
    plan = []

    needs_math = _task_needs_math(task_input)
    needs_summary = _task_needs_summary(task_input, '')
    needs_search = _task_needs_search(task_input, '')

    if needs_search and needs_math:
        plan.append({
            'step': 1,
            'capability': 'search',
            'instruction': task_input,
            'reason': 'Look up formula/information before computation',
        })

    if needs_math:
        plan.append({
            'step': len(plan) + 1,
            'capability': 'math',
            'instruction': task_input,
            'reason': 'Detected arithmetic expression in the task',
        })

    if needs_summary:
        plan.append({
            'step': len(plan) + 1,
            'capability': 'summarization',
            'instruction': _summary_instruction(''),
            'reason': 'Task requests a summary',
        })

    if needs_search and not plan:
        plan.append({
            'step': 1,
            'capability': 'search',
            'instruction': task_input,
            'reason': 'Detected search intent in user task',
        })

    if not plan:
        fallback_capability = requested_capability or 'search'
        if fallback_capability in {'orchestration', 'planning', 'task_decomposition'}:
            fallback_capability = 'search'
        plan.append({
            'step': 1,
            'capability': fallback_capability,
            'instruction': task_input,
            'reason': 'Fallback single-step plan',
        })

    return plan[:MAX_HOPS]


def _plan_with_llm(task_input, goal, agents, requested_capability):
    agent_inventory = [
        {
            'name': agent.get('name'),
            'capabilities': agent.get('capabilities', []),
        }
        for agent in agents
    ]

    system_prompt = (
        'You are an A2A planner. Create a concise execution plan in strict JSON only. '
        'Never include markdown. Use available capabilities only. '
        'Return JSON with this exact shape: '
        '{"plan": [{"step": 1, "capability": "math", "instruction": "...", "preferred_agent": "optional"}]}'
    )
    user_prompt = (
        f'User task: {task_input}\n'
        f'Goal: {goal or "(none)"}\n'
        f'Requested capability hint: {requested_capability or "(none)"}\n'
        f'Available agents: {json.dumps(agent_inventory)}\n'
        f'Rules: max {MAX_HOPS} steps, step numbers start from 1, and include at least 1 step.'
    )

    try:
        response = ollama.chat(
            model=ORCHESTRATOR_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        content = response.message.content
        parsed_json = _extract_json(content)
        if not parsed_json:
            return _post_process_plan([], task_input, goal, requested_capability, agents)

        data = json.loads(parsed_json)
        plan = data.get('plan', [])
        if not isinstance(plan, list) or not plan:
            return _post_process_plan([], task_input, goal, requested_capability, agents)

        return _post_process_plan(plan, task_input, goal, requested_capability, agents)
    except Exception as exc:
        logger.warning(f'LLM planning failed, using fallback planner: {exc}')
        return _post_process_plan([], task_input, goal, requested_capability, agents)


def _materialize_instruction(instruction, previous_result, original_task, capability):
    text = instruction or original_task
    if '{{previous_result}}' in text:
        text = text.replace('{{previous_result}}', str(previous_result))
    elif previous_result is not None and _is_summary_capability(capability):
        # Keep summarization input clean and centered on the computed output.
        if instruction and instruction != original_task:
            text = f"{instruction}\n\nInput result:\n{previous_result}"
        else:
            text = f"Summarize this result:\n{previous_result}"
    elif previous_result is not None:
        # Keep downstream steps context-rich even when the LLM rewrites the instruction.
        text = f'{text}\n\nPrevious step result:\n{previous_result}'
    return text


def _execute_plan(task_id, task_input, goal, agents, requested_capability, plan):
    hops = []
    previous_results = []
    last_result = None

    for step in plan:
        capability = step['capability']
        instruction = step.get('instruction', task_input)
        preferred_agent_name = (step.get('preferred_agent') or '').strip()

        candidates = _find_candidates(agents, capability)
        if preferred_agent_name:
            preferred = [a for a in candidates if a.get('name') == preferred_agent_name]
            non_preferred = [a for a in candidates if a.get('name') != preferred_agent_name]
            candidates = preferred + non_preferred

        if not candidates:
            return {
                'status': 'error',
                'error': f'No available agent for capability: {capability}',
                'hops': hops,
                'result': None,
            }

        step_input = _materialize_instruction(instruction, last_result, task_input, capability)
        step_succeeded = False
        step_error = None

        for attempt_index, agent in enumerate(candidates[:2], start=1):
            a2a_payload = {
                'task_id': task_id,
                'capability': capability,
                'input': step_input,
                'context': {
                    'goal': goal,
                    'step': deepcopy(step),
                    'previous_results': deepcopy(previous_results),
                    'attempt': attempt_index,
                    'a2a_version': '1.0',
                },
            }

            start = time.perf_counter()
            try:
                response = requests.post(f"{agent['endpoint_url']}/execute", json=a2a_payload, timeout=30)
                duration_ms = int((time.perf_counter() - start) * 1000)
                response_json = response.json()

                hop = {
                    'from': AGENT_INFO['name'],
                    'to': agent['name'],
                    'step': deepcopy(step),
                    'capability': capability,
                    'attempt': attempt_index,
                    'request': deepcopy(a2a_payload),
                    'response': response_json,
                    'http_status': response.status_code,
                    'duration_ms': duration_ms,
                    'status': 'success' if response.ok else 'error',
                }
                hops.append(hop)

                if response.ok and response_json.get('status') != 'error':
                    last_result = response_json.get('result')
                    previous_results.append(
                        {
                            'agent': agent['name'],
                            'capability': capability,
                            'result': last_result,
                        }
                    )
                    step_succeeded = True
                    break

                step_error = response_json.get('error') or f'HTTP {response.status_code}'
            except requests.RequestException as exc:
                duration_ms = int((time.perf_counter() - start) * 1000)
                hop = {
                    'from': AGENT_INFO['name'],
                    'to': agent['name'],
                    'step': deepcopy(step),
                    'capability': capability,
                    'attempt': attempt_index,
                    'request': deepcopy(a2a_payload),
                    'response': {'error': str(exc)},
                    'duration_ms': duration_ms,
                    'status': 'error',
                }
                hops.append(hop)
                step_error = str(exc)

        if not step_succeeded:
            return {
                'status': 'error',
                'error': f'Step {step.get("step")} failed: {step_error}',
                'hops': hops,
                'result': None,
            }

    return {
        'status': 'success',
        'error': None,
        'hops': hops,
        'result': last_result,
    }


@app.route('/execute', methods=['POST'])
def execute():
    data = request.get_json() or {}
    task_id = (data.get('task_id') or '').strip() or f"task_{uuid.uuid4().hex[:12]}"
    task_input = (data.get('input') or '').strip()
    context = data.get('context') or {}

    if not task_input:
        return jsonify({'task_id': task_id, 'status': 'error', 'result': None, 'error': 'Missing input', 'hops': []}), 400

    goal = (context.get('goal') or '').strip()
    requested_capability = (context.get('requested_capability') or data.get('capability') or '').strip().lower()
    available_agents = context.get('available_agents') or []

    logger.info(f'[{task_id}] Planning task with {len(available_agents)} available agents')

    plan = _plan_with_llm(task_input, goal, available_agents, requested_capability)
    outcome = _execute_plan(task_id, task_input, goal, available_agents, requested_capability, plan)

    response = {
        'task_id': task_id,
        'status': outcome['status'],
        'plan': plan,
        'result': outcome['result'],
        'error': outcome['error'],
        'hops': outcome['hops'],
    }
    return jsonify(response), 200 if outcome['status'] == 'success' else 502


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'agent': AGENT_INFO['name']})


def register(retries=8, delay=2):
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(f"{REGISTRY_URL}/api/agents/register", json=AGENT_INFO, timeout=5)
            if resp.ok:
                logger.info('Orchestrator registered with registry successfully')
                return
            logger.warning(f'Registry returned {resp.status_code} on orchestrator registration')
        except requests.ConnectionError:
            logger.warning(f'Registry not reachable (attempt {attempt}/{retries}), retrying in {delay}s...')
            time.sleep(delay)

    logger.error('Could not register orchestrator with registry after all retries')


if __name__ == '__main__':
    register()
    app.run(port=ORCHESTRATOR_PORT)
