import os
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

REGISTRY_URL = os.getenv('REGISTRY_URL', 'http://localhost:8020')

st.set_page_config(page_title="A2A Agent Marketplace", layout="wide")
st.title("A2A Agent Marketplace")

tab1, tab2 = st.tabs(["Agent Directory", "Send Task"])

with tab1:
    st.subheader("Registered Agents")
    if st.button("Refresh", key="refresh"):
        st.rerun()

    try:
        resp = requests.get(f"{REGISTRY_URL}/api/agents/list", timeout=5)
        data = resp.json()
        agents = data.get('agents', [])

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
        st.error("Cannot reach the registry. Is it running on port 8020?")

with tab2:
    st.subheader("Send a Task to an Agent")

    try:
        resp = requests.get(f"{REGISTRY_URL}/api/agents/list", timeout=5)
        agents = resp.json().get('agents', [])
        agent_names = [a['name'] for a in agents]
    except Exception:
        agents, agent_names = [], []

    if not agent_names:
        st.warning("No agents available. Register agents first.")
    else:
        agent_name = st.selectbox("Select Agent", agent_names)
        selected = next((a for a in agents if a['name'] == agent_name), None)

        if selected:
            st.caption(f"Endpoint: {selected['endpoint_url']} | Capabilities: {', '.join(selected['capabilities'])}")

        task_input = st.text_area("Task input", placeholder="e.g. 25 * 4 + 10  (for math)  or  paste text to summarize")
        capability = st.text_input("Capability to request", value=selected['capabilities'][0] if selected else "math")

        if st.button("Send Task", type="primary"):
            if not task_input.strip():
                st.warning("Please enter a task.")
            else:
                with st.spinner("Waiting for agent response..."):
                    try:
                        payload = {'input': task_input, 'capability': capability, 'task_id': 'ui_task'}
                        resp = requests.post(f"{REGISTRY_URL}/api/orchestrate", json=payload, timeout=30)
                        result = resp.json()

                        if resp.ok:
                            agent_result = result.get('result', {})
                            st.success(f"**Agent:** {result.get('agent', 'Unknown')}")
                            st.markdown(f"**Result:** {agent_result.get('result', agent_result)}")
                        else:
                            st.error(f"Error: {result.get('error', 'Unknown error')}")
                    except requests.ConnectionError:
                        st.error("Cannot reach registry or agent.")
