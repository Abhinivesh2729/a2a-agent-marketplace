"""
MCP Gateway — registers itself as an agent, receives A2A requests,
and routes them internally to mock MCP tools.
"""
import uuid
import logging
import requests
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

REGISTRY_URL = "http://localhost:8020"
AGENT_INFO = {
    "name": "MCP Web Search Gateway",
    "description": "Gateway that translates A2A requests to MCP tool calls (web search)",
    "capabilities": ["search", "web_search", "lookup"],
    "endpoint": "http://localhost:8003",
}

# Mock MCP tool — returns realistic-looking search results without external API
_MOCK_RESULTS = {
    "python": "Python is a high-level programming language known for simplicity and readability.",
    "django": "Django is a high-level Python web framework that encourages rapid development.",
    "machine learning": "Machine learning is a subset of AI that allows systems to learn from data.",
    "default": "Search completed. Here are the most relevant results for your query.",
}


def mcp_web_search(query):
    for key, result in _MOCK_RESULTS.items():
        if key in query.lower():
            return result
    return _MOCK_RESULTS["default"] + f" Query: {query}"


@app.route('/execute', methods=['POST'])
def execute():
    data = request.get_json()
    task_id = data.get('task_id', str(uuid.uuid4()))
    query = data.get('input', '')

    logger.info(f"[{task_id}] MCP gateway received search: {query}")

    result = mcp_web_search(query)
    return jsonify({'task_id': task_id, 'status': 'success', 'result': result, 'error': None})


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'agent': AGENT_INFO['name']})


def register():
    try:
        resp = requests.post(f"{REGISTRY_URL}/api/agents/register", json=AGENT_INFO, timeout=5)
        if resp.ok:
            logger.info("Gateway registered with registry")
    except requests.ConnectionError:
        logger.warning("Registry not reachable at startup")


if __name__ == '__main__':
    register()
    app.run(port=8003)
