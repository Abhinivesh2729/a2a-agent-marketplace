import os
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

REGISTRY_URL = os.getenv('REGISTRY_URL', 'http://localhost:8000')

st.set_page_config(page_title="A2A Agent Marketplace", layout="wide")
st.title("A2A Agent Marketplace")


def fetch_agents():
    resp = requests.get(f"{REGISTRY_URL}/api/agents/list", timeout=5)
    resp.raise_for_status()
    return resp.json().get('agents', [])


def fetch_trace(task_id):
    resp = requests.get(f"{REGISTRY_URL}/api/traces/{task_id}", timeout=8)
    resp.raise_for_status()
    return resp.json()


def extract_final_output(result_payload):
    if isinstance(result_payload, dict):
        if isinstance(result_payload.get('result'), dict):
            nested = result_payload.get('result', {})
            return nested.get('result', nested)
        return result_payload.get('result', result_payload)
    return result_payload


def render_hops(trace):
    hops = trace.get('hops', [])
    if not hops:
        st.info('No A2A hops recorded yet for this task.')
        return

    for idx, hop in enumerate(hops, start=1):
        status = str(hop.get('status', 'unknown')).upper()
        marker = '[OK]' if hop.get('status') == 'success' else '[WARN]'
        source = hop.get('from', 'unknown')
        target = hop.get('to', 'unknown')
        capability = hop.get('capability', '-')
        duration = hop.get('duration_ms', '-')
        with st.expander(f"{marker} Hop {idx}: {source} -> {target} | {capability} | {status} | {duration}ms"):
            st.markdown('**A2A Request**')
            st.json(hop.get('request', {}), expanded=False)
            st.markdown('**A2A Response**')
            st.json(hop.get('response', {}), expanded=False)


tab1, tab2, tab3 = st.tabs(["Agent Directory", "Send Task", "Task Traces"])

with tab1:
    st.subheader("Registered Agents")
    if st.button("Refresh", key="refresh"):
        st.rerun()

    try:
        agents = fetch_agents()

        if not agents:
            st.info("No agents registered yet. Start the agent services to register them.")
        else:
            for agent in agents:
                with st.container(border=True):
                    col1, col2 = st.columns([3, 1])
                    col1.markdown(f"**{agent['name']}**")
                    status_color = "green" if agent['status'] == 'active' else "red"
                    col2.markdown(f":{status_color}[{agent['status'].upper()}]")
                    st.caption(agent['description'])
                    caps = " • ".join(f"`{c}`" for c in agent['capabilities'])
                    st.markdown(f"Capabilities: {caps}")
                    st.caption(f"Endpoint: {agent['endpoint_url']}")
    except requests.ConnectionError:
        st.error("Cannot reach the registry. Is it running on port 8000?")
    except requests.RequestException as exc:
        st.error(f"Failed to load agents: {exc}")

with tab2:
    st.subheader("Send a Task")
    st.caption("Use Auto mode for orchestrated multi-agent execution via A2A.")

    try:
        agents = fetch_agents()
        agent_names = [a['name'] for a in agents]
    except Exception:
        agents, agent_names = [], []

    if not agent_names:
        st.warning("No agents available. Register agents first.")
    else:
        default_index = 0
        agent_options = ['Auto'] + agent_names
        agent_choice = st.selectbox("Select Agent", agent_options, index=default_index)
        is_auto = agent_choice == 'Auto'
        selected = next((a for a in agents if a['name'] == agent_choice), None)

        if selected and not is_auto:
            st.caption(f"Endpoint: {selected['endpoint_url']} | Capabilities: {', '.join(selected['capabilities'])}")

        task_input = st.text_area(
            "Task input",
            placeholder="e.g. calculate 2^10 - 2^8 and summarize the result",
        )

        goal = ''
        if is_auto:
            goal = st.text_input(
                "Goal (optional)",
                placeholder="e.g. Solve accurately, then explain briefly",
            )

        default_capability = 'orchestration' if is_auto else (selected['capabilities'][0] if selected else 'math')
        capability = st.text_input("Capability to request", value=default_capability)

        if st.button("Send Task", type="primary"):
            if not task_input.strip():
                st.warning("Please enter a task.")
            else:
                with st.spinner("Waiting for agent response..."):
                    try:
                        payload = {
                            'input': task_input,
                            'goal': goal,
                            'capability': capability,
                            'selection_mode': 'auto' if is_auto else 'specific',
                        }
                        if not is_auto:
                            payload['agent_name'] = agent_choice

                        resp = requests.post(f"{REGISTRY_URL}/api/orchestrate", json=payload, timeout=180)
                        result = resp.json()

                        if resp.ok:
                            task_id = result.get('task_id')
                            st.success(f"Task completed. Task ID: {task_id}")
                            st.markdown(f"**Handled by:** {result.get('agent', 'Unknown')}")

                            final_output = extract_final_output(result.get('result'))
                            st.markdown("### Final Output")
                            st.info(str(final_output))

                            plan = result.get('plan') or result.get('result', {}).get('plan') or []
                            if plan:
                                st.markdown("### Orchestration Plan")
                                for step in plan:
                                    st.markdown(
                                        f"Step {step.get('step')}: `{step.get('capability')}` - {step.get('instruction', '')}"
                                    )

                            if task_id:
                                st.markdown("### A2A Request Trace")
                                try:
                                    trace = fetch_trace(task_id)
                                    render_hops(trace)
                                except requests.RequestException as exc:
                                    st.warning(f"Task completed but trace could not be loaded: {exc}")
                        else:
                            st.error(f"Error: {result.get('error', 'Unknown error')}")
                            if result.get('trace_id'):
                                st.markdown("### A2A Request Trace (Partial)")
                                try:
                                    trace = fetch_trace(result['trace_id'])
                                    render_hops(trace)
                                except requests.RequestException:
                                    pass
                    except requests.ConnectionError:
                        st.error("Cannot reach registry or agent.")
                    except requests.RequestException as exc:
                        st.error(f"Request failed: {exc}")

with tab3:
    st.subheader("Recent Task Traces")
    st.caption("Review A2A hops for recently executed tasks.")

    try:
        traces_resp = requests.get(f"{REGISTRY_URL}/api/traces", timeout=8)
        traces_resp.raise_for_status()
        traces = traces_resp.json().get('traces', [])

        if not traces:
            st.info("No traces found yet.")
        else:
            selected_task_id = st.selectbox(
                "Select Task ID",
                [t['task_id'] for t in traces],
                key='trace_selector',
            )

            selected_trace = fetch_trace(selected_task_id)
            st.markdown(
                f"**Status:** {selected_trace.get('status', '').upper()} | "
                f"**Mode:** {selected_trace.get('selection_mode', '').upper()}"
            )
            st.markdown(f"**Input:** {selected_trace.get('user_input', '')}")
            if selected_trace.get('goal'):
                st.markdown(f"**Goal:** {selected_trace.get('goal')}")

            st.markdown("### A2A Hops")
            render_hops(selected_trace)
    except requests.ConnectionError:
        st.error("Cannot reach the registry. Is it running on port 8000?")
    except requests.RequestException as exc:
        st.error(f"Failed to load traces: {exc}")
