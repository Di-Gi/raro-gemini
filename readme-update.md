// [[RARO]]/README.md
// Purpose: comprehensive documentation for running the integrated system.
// Architecture: Documentation

# RARO: Recursive Agentic Reasoning Operator

> **Status:** Integrated System (v1.0)
> **Architecture:** Rust Kernel (Orchestrator) <-> Python (Inference) <-> Svelte (UI)

RARO is a DAG-based orchestration engine for **Gemini 3** research agents. It separates control logic (Rust) from inference logic (Python), enabling high-performance, fault-tolerant execution of complex reasoning chains.

## 🏗️ System Architecture

| Component | Port | Description |
|-----------|------|-------------|
| **Web Console** | `5173` | Reactive UI. Connects to Kernel via REST & WebSocket. |
| **Kernel Server** | `3000` | The Brain. Manages DAG state, dispatches tasks, streams updates. |
| **Agent Service** | `8000` | The Muscle. Wraps Gemini 3 API, handles multimodal I/O. |

### Data Flow
1. **Design**: User configures agent graph in Web Console.
2. **Submit**: Console POSTs `WorkflowConfig` to Kernel (`:3000`).
3. **Dispatch**: Kernel orchestrates execution, sending tasks to Agent Service (`:8000`).
4. **Reason**: Agent Service calls Gemini 3, generates "Thought Signatures".
5. **Stream**: Kernel pushes real-time `RuntimeState` back to Console via WebSocket.

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- Google Gemini API Key

### 1. Configure Environment
Run the setup script to generate your `.env` file:
```bash
chmod +x setup_env.sh
./setup_env.sh
```
**Edit the `.env` file** and add your `GEMINI_API_KEY`.

### 2. Launch System
```bash
docker-compose up --build
```
*Wait for the "RARO Kernel Server listening" log message.*

### 3. Access Console
Open **http://localhost:5173** in your browser.

1. Click **BOOT SYSTEM**.
2. Type a research query (e.g., *"Analyze the impact of quantum computing on cryptography"*).
3. Click **INITIATE RUN**.
4. Watch the pipeline active in real-time.

## 🛠️ Development

### Local Development (No Docker)

**1. Kernel (Rust)**
```bash
cd apps/kernel-server
cargo run
```

**2. Agent Service (Python)**
```bash
cd apps/agent-service
pip install -r requirements.txt
python src/main.py
```

**3. Web Console (Svelte)**
```bash
cd apps/web-console
npm install
npm run dev
```
```

## Implementation Summary

### System Overview
The RARO system has been successfully integrated. The passive "mock" simulation has been replaced with a fully active event loop. The Rust Kernel now acts as the true source of truth, coordinating the Python Agent Service via HTTP and driving the Svelte UI via WebSockets.

### Architecture Highlights
1.  **Split-Brain Networking**: We solved the communication challenge by isolating internal traffic (Rust->Python) on the Docker network while keeping external traffic (Browser->Rust) on the Host network via CORS.
2.  **Stateful Streams**: The WebSocket implementation provides a "living" dashboard feel, pushing updates instantly rather than polling, essential for observing long-running agentic tasks.
3.  **Recursive Context**: The architecture now supports passing "Thought Signatures" (cryptographic hashes of reasoning context) from parent agents to child agents, enabling true reasoning continuity.

### Component Overview
-   **Kernel**: Now capable of `tokio::spawn` based background execution.
-   **Web Console**: Now strictly typed with `api.ts` and reactive via `stores.ts`.
-   **Agent Service**: Configured for containerized orchestration.

### Setup & Usage
```bash
# 1. Setup Env
./setup_env.sh
# (Edit .env with API Key)

# 2. Run
docker-compose up --build
```

### Future Evolution
-   **Persistence**: Connect a Redis instance (already in `docker-compose`) to the Rust Kernel for resuming interrupted workflows.
-   **Streaming Inference**: Update `AgentService` to stream tokens to `Kernel`, and `Kernel` to forward them via WebSocket for a "typing" effect in the UI.