import ast
import operator
import logging
import os
import re
import requests
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

REGISTRY_URL = os.getenv('REGISTRY_URL', 'http://localhost:8000')
AGENT_INFO = {
    "name": "Math Helper",
    "description": "Solves basic arithmetic and math expressions",
    "capabilities": ["math", "calculator", "arithmetic"],
    "endpoint": "http://localhost:8001",
}

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


def _safe_eval(expr):
    def _eval(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            return _SAFE_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            return _SAFE_OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"Unsupported expression: {ast.dump(node)}")

    tree = ast.parse(expr.strip(), mode='eval')
    return _eval(tree.body)


def _normalize_expression(text):
    raw = (text or '').strip().lower()
    if not raw:
        raise ValueError('No expression provided')

    # Normalize common natural-language operator forms.
    replacements = {
        'multiplied by': '*',
        'times': '*',
        'divided by': '/',
        'plus': '+',
        'minus': '-',
        'to the power of': '**',
        'power': '**',
        '^': '**',
    }
    for old, new in replacements.items():
        raw = raw.replace(old, new)

    # Special-case phrase patterns like "sum of A and B".
    sum_match = re.search(r'sum of\s+([\d\.]+)\s+and\s+([\d\.]+)', raw)
    if sum_match:
        return f"{sum_match.group(1)}+{sum_match.group(2)}"

    # Keep only arithmetic tokens and separators, then extract true expressions.
    cleaned = re.sub(r'[^0-9\+\-\*\/\(\)\.%\s]', ' ', raw)
    expr_pattern = re.compile(
        r'(?:\d+(?:\.\d+)?|\([^\)]+\))\s*(?:\*\*|[\+\-\*\/%])\s*(?:\d+(?:\.\d+)?|\([^\)]+\))'
        r'(?:\s*(?:\*\*|[\+\-\*\/%])\s*(?:\d+(?:\.\d+)?|\([^\)]+\)))*'
    )
    candidates = [match.group(0).strip() for match in expr_pattern.finditer(cleaned)]

    if not candidates:
        raise ValueError('No arithmetic expression found in input')

    # Prefer the longest valid-looking arithmetic chunk.
    expr = max(candidates, key=len)
    expr = re.sub(r'\s+', ' ', expr).strip()

    if not re.search(r'\d', expr) or not re.search(r'[\+\-\*\/%]', expr):
        raise ValueError(f'No valid arithmetic operators found in input: {text}')

    return expr


@app.route('/execute', methods=['POST'])
def execute():
    data = request.get_json()
    task_id = data.get('task_id', 'unknown')
    raw_input = data.get('input', '')

    logger.info(f"[{task_id}] Math task: {raw_input}")

    try:
        expr = _normalize_expression(raw_input)
        result = _safe_eval(expr)
        return jsonify({'task_id': task_id, 'status': 'success', 'result': str(result), 'error': None})
    except Exception as e:
        return jsonify({'task_id': task_id, 'status': 'error', 'result': None, 'error': str(e)}), 400


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
    app.run(port=8001)
