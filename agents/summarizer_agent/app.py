import logging
import requests
import ollama
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

REGISTRY_URL = "http://localhost:8020"
AGENT_INFO = {
    "name": "Text Summarizer",
    "description": "Summarizes text using a local language model",
    "capabilities": ["summarization", "text", "nlp"],
    "endpoint": "http://localhost:8002",
}


def summarize(text):
    response = ollama.chat(
        model='qwen2.5:3b',
        messages=[
            {'role': 'system', 'content': 'You are a concise text summarizer. Return only the summary, no preamble.'},
            {'role': 'user', 'content': f"Summarize this in 2-3 sentences:\n\n{text}"},
        ]
    )
    return response.message.content.strip()


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


def register():
    try:
        resp = requests.post(f"{REGISTRY_URL}/api/agents/register", json=AGENT_INFO, timeout=5)
        if resp.ok:
            logger.info("Registered with registry successfully")
        else:
            logger.warning(f"Registry returned {resp.status_code}")
    except requests.ConnectionError:
        logger.warning("Registry not reachable, skipping registration")


if __name__ == '__main__':
    register()
    app.run(port=8002)
