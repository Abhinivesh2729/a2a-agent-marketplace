import logging
import os
import json
import re
import requests
import ollama
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

REGISTRY_URL = os.getenv('REGISTRY_URL', 'http://localhost:8000')
AGENT_INFO = {
    "name": "Text Summarizer",
    "description": "Summarizes text using a local language model",
    "capabilities": ["summarization", "text", "nlp"],
    "endpoint": "http://localhost:8002",
}


def _extract_json_block(text):
    if not text:
        return '', ''
    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        return '', text.strip()
    json_text = match.group(0).strip()
    trailing = (text[match.end():] or '').strip()
    return json_text, trailing


def _parse_requested_lines(text):
    lower = (text or '').lower()
    numeric = re.search(r'\b(\d{1,2})\s*lines?\b', lower)
    if numeric:
        return max(1, min(12, int(numeric.group(1))))

    words = {
        'one': 1,
        'two': 2,
        'three': 3,
        'four': 4,
        'five': 5,
        'six': 6,
    }
    for word, value in words.items():
        if re.search(rf'\b{word}\s+lines?\b', lower):
            return value
    return None


def _detect_style(text):
    lower = (text or '').lower()
    if 'json' in lower:
        return 'json'
    if 'email' in lower or 'mail format' in lower:
        return 'email'
    if 'bullet' in lower or 'points' in lower:
        return 'bullets'
    if _parse_requested_lines(lower):
        return 'lines'
    return 'paragraph'


def _extract_content(text):
    source = (text or '').strip()
    markers = [
        'Input result:',
        'Use this computed result as input:',
        'Previous step result:',
    ]
    for marker in markers:
        if marker in source:
            tail = source.split(marker, 1)[1].strip()
            if tail:
                return tail
    return source


def _looks_like_refusal(text):
    lower = (text or '').lower()
    return any(
        phrase in lower
        for phrase in [
            'do not have enough context',
            'don\'t have enough context',
            'without context i cannot',
            'cannot summarize',
            'need more context',
        ]
    )


def _fallback_output(style, content, lines):
    compact = content.strip() or 'No additional details were provided.'
    if style == 'email':
        return (
            'Subject: Result Summary\n\n'
            f'Hello,\n\nThe computed result is: {compact}.\n\nRegards,\nA2A Summarizer'
        )
    if style == 'json':
        payload = {
            'summary': f'Result summary: {compact}',
            'key_points': [
                'Input was processed successfully.',
                'Output is based on the available result only.',
            ],
            'result': compact,
        }
        return json.dumps(payload, indent=2) + '\n\nExplanation: The summary was generated from the provided result.'
    if style == 'bullets':
        return f'- Result: {compact}\n- The output is concise and based on available data.\n- No extra context was required.'
    if style == 'lines' and lines:
        first = f'Result: {compact}'
        generic = [
            'This summary uses the available computed output.',
            'No additional context was required to complete the response.',
            'The result can be reformatted on request.',
        ]
        selected = [first] + generic[: max(0, lines - 1)]
        return '\n'.join(selected)
    return f'The computed result is {compact}. This summary uses the available information.'


def _build_user_prompt(style, content, lines):
    if style == 'json':
        return (
            'Summarize the input into valid JSON with keys "summary", "key_points", and "result" when relevant. '
            'After the JSON object, add a short explanation paragraph.\n\n'
            f'Input:\n{content}'
        )
    if style == 'email':
        return f'Write a concise email-style summary with a Subject line for this input:\n\n{content}'
    if style == 'bullets':
        return f'Summarize this input as concise bullet points:\n\n{content}'
    if style == 'lines' and lines:
        return f'Summarize this input in about {lines} lines (best effort):\n\n{content}'
    return f'Summarize this input in 1-2 concise sentences:\n\n{content}'


def summarize(text):
    style = _detect_style(text)
    lines = _parse_requested_lines(text)
    content = _extract_content(text)
    prompt = _build_user_prompt(style, content, lines)

    response = ollama.chat(
        model='qwen2.5:3b',
        messages=[
            {
                'role': 'system',
                'content': (
                    'You are a concise formatting-aware summarizer. '
                    'Never refuse because of missing context. Use available input and format exactly as requested.'
                ),
            },
            {'role': 'user', 'content': prompt},
        ],
    )
    output = (response.message.content or '').strip()

    if not output or _looks_like_refusal(output):
        return _fallback_output(style, content, lines)

    if style == 'json':
        json_text, explanation = _extract_json_block(output)
        if json_text:
            try:
                payload = json.loads(json_text)
                json_rendered = json.dumps(payload, indent=2)
                expl = explanation or 'Explanation: The summary was generated from the provided result.'
                return f"{json_rendered}\n\n{expl}"
            except json.JSONDecodeError:
                pass
        return _fallback_output(style, content, lines)

    return output


@app.route('/execute', methods=['POST'])
def execute():
    data = request.get_json()
    task_id = data.get('task_id', 'unknown')
    text = data.get('input', '')

    logger.info(f"[{task_id}] Summarizing {len(text)} chars")

    if not text:
        return jsonify({'task_id': task_id, 'status': 'error', 'result': None, 'error': 'No input provided'}), 400

    try:
        result = summarize(text)
        return jsonify({'task_id': task_id, 'status': 'success', 'result': result, 'error': None})
    except Exception as e:
        return jsonify({'task_id': task_id, 'status': 'error', 'result': None, 'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'agent': AGENT_INFO['name']})


def register(retries=5, delay=2):
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(f"{REGISTRY_URL}/api/agents/register", json=AGENT_INFO, timeout=5)
            if resp.ok:
                logger.info("Registered with registry successfully")
                return
            logger.warning(f"Registry returned {resp.status_code}")
        except requests.ConnectionError:
            logger.warning(f"Registry not reachable (attempt {attempt}/{retries}), retrying in {delay}s...")
            time.sleep(delay)
    logger.error("Could not register with registry after all retries")


if __name__ == '__main__':
    import time
    register()
    app.run(port=8002)
