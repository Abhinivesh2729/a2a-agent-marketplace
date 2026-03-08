# A2A Agent Marketplace with MCP Gateway

An agent directory where AI agents register themselves, communicate using A2A (Agent-to-Agent) protocol, and a gateway bridges MCP tools into the marketplace.

## Architecture

```
Streamlit UI
     │
     ▼
Registry (Django :8020)        ← agents register here
  │  POST /api/orchestrate (specific or auto)
     │
     ├──► Math Agent (Flask :8001)       → evaluates math expressions
     ├──► Summarizer Agent (Flask :8002) → summarizes text via Ollama
     └──► MCP Gateway (Flask :8003)      → wraps MCP tool as an A2A agent
  └──► Task Orchestrator (Flask :8004)→ plans and chains A2A calls across agents
```

## A2A Message Format

**Agent Registration:**
```json
{ "name": "Math Helper", "description": "...", "capabilities": ["math"], "endpoint": "http://localhost:8001" }
```

**Task Request (POST /execute on any agent):**
```json
{ "task_id": "123", "capability": "math", "input": "25 * 4", "context": {} }
```

**Task Response:**
```json
{ "task_id": "123", "status": "success", "result": "100", "error": null }
```

## Prerequisites

- Python 3.10+, PostgreSQL 15, Ollama with `qwen2.5:3b`

## Setup

**1. Install dependencies**
```bash
pip install -r requirements.txt
```

**2. Create database**
```bash
psql -U apple -d postgres -c "CREATE DATABASE agent_marketplace;"
```

**3. Run migrations**
```bash
cd registry
DJANGO_SETTINGS_MODULE=config.settings python manage.py migrate
```

## Running (4 terminals)

**Terminal 1 — Registry**
```bash
cd registry
DJANGO_SETTINGS_MODULE=config.settings python manage.py runserver 8020
```

**Terminal 2 — Math Agent**
```bash
cd agents/math_agent
python app.py
```

**Terminal 3 — Summarizer Agent**
```bash
cd agents/summarizer_agent
python app.py
```

**Terminal 4 — MCP Gateway**
```bash
cd gateway
python app.py
```

**Terminal 5 — Orchestrator Agent**
```bash
cd agents/orchestrator_agent
python app.py
```

**Terminal 6 — Streamlit UI**
```bash
cd ui
streamlit run app.py --server.port 8512
```

Open http://localhost:8512

## Testing via curl

```bash
# List all agents
curl http://localhost:8020/api/agents/list

# Send math task
curl -X POST http://localhost:8020/api/orchestrate \
  -H "Content-Type: application/json" \
  -d '{"input": "25 * 4 + 10", "capability": "math"}'

# Send auto-orchestrated multi-agent task
curl -X POST http://localhost:8020/api/orchestrate \
  -H "Content-Type: application/json" \
  -d '{"input": "calculate 2^10 - 2^8 and summarize the answer", "goal": "compute then summarize", "selection_mode": "auto"}'

# Send summarization task
curl -X POST http://localhost:8020/api/orchestrate \
  -H "Content-Type: application/json" \
  -d '{"input": "Django is a web framework...", "capability": "summarization"}'

# Search by capability
curl "http://localhost:8020/api/agents/search?capability=math"

# List recent traces
curl "http://localhost:8020/api/traces"

# Get one trace with all A2A hops
curl "http://localhost:8020/api/traces/<task_id>"
```

## Auto Orchestration Notes

- `Auto` mode sends the task to the `Task Orchestrator` agent.
- The orchestrator plans a capability sequence and executes each step using A2A `POST /execute` calls.
- If a step fails, it tries one alternate active agent with the same capability before aborting.
- Every hop (request + response) is stored as a trace and visible in the Streamlit UI.
