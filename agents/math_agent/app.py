import ast
import operator
import logging
import requests
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

REGISTRY_URL = "http://localhost:8020"
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


@app.route('/execute', methods=['POST'])
def execute():
    data = request.get_json()
    task_id = data.get('task_id', 'unknown')
    expr = data.get('input', '')

    logger.info(f"[{task_id}] Math task: {expr}")

    try:
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
