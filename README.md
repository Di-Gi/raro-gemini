# RARO: Reconfigurable Agentic Runtime Operator

A production-grade, multi-agent orchestration platform for Gemini 3 API built for the Gemini 3 Developer Competition Hackathon.

**Status**: Core infrastructure complete. Ready for Gemini 3 integration and agent implementation.

## Project Vision

RARO is a **"Kubernetes for Cognitive Workflows"** — a visual, interactive control plane where researchers and developers configure how multiple Gemini 3-powered agents cooperate to perform complex research synthesis tasks.

### Key Differentiators

- **Agentic Runtime as the Product**: Not just a research app, but a reusable orchestrated multi-agent runtime
- **Multimodal Research Synthesis**: PDFs + figures + code + recorded talks as first-class inputs
- **Thought Signature Preservation**: Gemini 3's unique reasoning continuity across agent boundaries
- **Reconfigurable Architecture**: Hot-reload agents and workflows without restarts
- **Production Ready**: Type-safe, observable, scalable

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│            SvelteKit Web Console (UI)               │
│  • Interactive workflow canvas                      │
│  • Real-time agent monitoring                       │
│  • Component configuration                          │
└────────────────────┬────────────────────────────────┘
                     │ WebSocket
┌────────────────────▼────────────────────────────────┐
│       Rust Kernel (Orchestrator Layer)              │
│  • DAG scheduling & validation                      │
│  • Thought signature management                     │
│  • Runtime state coordination                       │
│  • WebSocket streaming                              │
└────────────────────┬────────────────────────────────┘
                     │ gRPC
┌────────────────────▼────────────────────────────────┐
│    Python Agent Service (Execution Layer)           │
│  • Gemini 3 API integration                         │
│  • Multi-agent orchestration                        │
│  • Tool invocation                                  │
│  • Context caching                                  │
└─────────────────────────────────────────────────────┘
```

### Stack Selection

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Frontend** | **SvelteKit 5 + TypeScript** | Best-in-class reactivity for high-frequency graph updates; Runes for reactive state |
| **Control Plane** | **Rust (Axum + Tokio)** | Strict concurrency, low-latency WebSocket, rock-solid reliability |
| **Agent Plane** | **Python (FastAPI)** | Gemini 3 SDK, LangGraph, Pydantic for AI ecosystem integration |
| **Communication** | **gRPC + WebSockets** | gRPC for service-to-service; WebSockets for real-time frontend streaming |
| **Persistence** | **PostgreSQL + Redis** | PostgreSQL for audit logs; Redis for thought signature caching |

## Project Structure

```
/raro-monorepo
├── Cargo.toml                  # Rust workspace root
├── package.json                # npm workspace root
├── docker-compose.yml          # Local development environment
├── README.md                   # This file
│
├── apps/
│   ├── web-console/            # SvelteKit UI
│   │   ├── src/
│   │   │   ├── components/     # Svelte components
│   │   │   ├── lib/            # Stores, utilities
│   │   │   ├── App.svelte      # Root component
│   │   │   └── main.ts         # Entry point
│   │   ├── vite.config.ts
│   │   ├── tsconfig.json
│   │   ├── svelte.config.js
│   │   └── package.json
│   │
│   ├── kernel-server/          # Rust orchestrator
│   │   ├── src/
│   │   │   ├── main.rs         # Server entry point
│   │   │   ├── models.rs       # Data models
│   │   │   ├── dag.rs          # DAG scheduler
│   │   │   ├── runtime.rs      # Runtime state
│   │   │   ├── server.rs       # HTTP handlers
│   │   │   └── observability.rs # Metrics
│   │   ├── Cargo.toml
│   │   └── Dockerfile
│   │
│   └── agent-service/          # Python agents
│       ├── src/
│       │   └── main.py         # FastAPI app
│       ├── requirements.txt
│       └── Dockerfile
│
└── guidance/                   # Reference documentation
    ├── research.md             # Hackathon research & ideas
    └── repo.md                 # Technical stack rationale

```

## Getting Started

### Prerequisites

- **Docker & Docker Compose** (recommended)
- Or:
  - Rust 1.70+
  - Node.js 18+
  - Python 3.11+
  - Gemini 3 API Key

### Quick Start (Docker)

1. **Set environment variables**:
   ```bash
   export GEMINI_API_KEY="your-api-key-here"
   ```

2. **Start the full stack**:
   ```bash
   docker-compose up -d
   ```

3. **Access the console**:
   - **UI**: http://localhost:5173
   - **Kernel API**: http://localhost:3000
   - **Agent Service**: http://localhost:8000

### Local Development

#### 1. Start the Rust Kernel

```bash
cd apps/kernel-server
cargo build --release
cargo run --release
# Kernel listening on http://127.0.0.1:3000
```

#### 2. Start the Python Agent Service

```bash
cd apps/agent-service
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python src/main.py
# Agent Service listening on http://0.0.0.0:8000
```

#### 3. Start the SvelteKit Web Console

```bash
cd apps/web-console
npm install
npm run dev
# Web Console listening on http://localhost:5173
```

## Core Components

### 1. Rust Kernel (`apps/kernel-server`)

**Responsibilities**:
- DAG validation and topological sorting
- Runtime orchestration
- Thought signature management
- WebSocket streaming to frontend

**Key Files**:
- `dag.rs`: Directed Acyclic Graph with cycle detection
- `runtime.rs`: Runtime state machine and coordination
- `models.rs`: Strongly-typed data models
- `server/handlers.rs`: HTTP/REST API endpoints

### 2. Python Agent Service (`apps/agent-service`)

**Responsibilities**:
- Gemini 3 API integration
- Tool invocation
- Model variant selection (Flash, Pro, Deep Think)
- Thought signature handling

**Key Files**:
- `src/main.py`: FastAPI application with agent endpoints

### 3. SvelteKit Web Console (`apps/web-console`)

**Responsibilities**:
- Interactive workflow visualization
- Real-time agent monitoring
- Node configuration UI
- Live reasoning trace display

**Key Components**:
- `OutputPane.svelte`: Live log stream
- `PipelineStage.svelte`: Interactive DAG canvas
- `ControlDeck.svelte`: Configuration & telemetry

## API Reference

### Kernel Server (Port 3000)

#### Start Workflow
```bash
POST /runtime/start
Content-Type: application/json

{
  "id": "workflow-123",
  "name": "Research_Synthesis",
  "agents": [
    {
      "id": "orchestrator",
      "role": "orchestrator",
      "model": "gemini-3-pro",
      "tools": ["plan_task"],
      "depends_on": []
    }
  ],
  "max_token_budget": 128000,
  "timeout_ms": 15000
}
```

#### Get Runtime State
```bash
GET /runtime/state?run_id=abc123
```

#### Get Thought Signatures
```bash
GET /runtime/signatures?run_id=abc123
```

### Agent Service (Port 8000)

#### Invoke Agent
```bash
POST /invoke
Content-Type: application/json

{
  "agent_id": "orchestrator",
  "model": "gemini-3-pro",
  "prompt": "Determine optimal sub-tasks for analysis",
  "input_data": {"context": "..."},
  "tools": ["plan_task", "route_agents"],
  "thought_signature": null
}
```

#### List Available Agents
```bash
GET /agents/list
```

#### List Available Models
```bash
GET /models/available
```

## Key Design Patterns

### 1. Thought Signature Transport

Gemini 3's unique feature for reasoning continuity:

```
Agent A → Tool 1 → Result + ThoughtSignature_A
Client returns: ToolResult + ThoughtSignature_A (exactly as received)
Agent B → Remembers Agent A's reasoning via Signature_A
```

**Implementation**: Rust kernel stores signatures in Redis with TTL until workflow completion.

### 2. Orchestrated Coordination

Central orchestrator coordinates all agents (vs. peer-to-peer choreography):

```
Orchestrator (Gemini 3 Pro) ← Decides flow
    ├─→ Extractor (Flash) → Returns with signature
    ├─→ KG Builder (Deep Think) → Returns with signature
    └─→ Synthesizer (Pro) → Returns with signature
```

### 3. Hot Reload Architecture

Users can reconfigure agents mid-execution:

1. User modifies agent config in UI
2. UI sends update to Rust kernel
3. Kernel validates and updates in-memory DAG
4. Specific nodes re-execute with new config

## Next Steps: Gemini 3 Integration

### Phase 1: Agent Implementations (Hours 1-8)

- [ ] Implement `invoke_agent()` in Python service with Gemini 3 API
- [ ] Handle thought signature extraction & storage
- [ ] Implement model variant selection (Flash/Pro/Deep Think)
- [ ] Add structured output validation with Pydantic

### Phase 2: Research Workflow (Hours 8-24)

- [ ] Build PDF extraction agent (Gemini 3 multimodal)
- [ ] Build video analysis agent
- [ ] Build knowledge graph builder (Deep Think)
- [ ] Build hypothesis generator

### Phase 3: Demo & Polish (Hours 24-36)

- [ ] Create demo workflow with 3-5 sample papers
- [ ] Generate knowledge graph visualization
- [ ] Record demo video
- [ ] Document output formats

### Phase 4: Submission (Hours 36-48)

- [ ] Polish UI/UX
- [ ] Write comprehensive README
- [ ] Create GitHub repository
- [ ] Prepare DevPost submission

## Development Workflow

### Before Pushing Code

```bash
# Type check Rust
cargo check

# Type check TypeScript
npm run check --workspace=apps/web-console

# Format code
cargo fmt
npm run format --workspaces
```

### Running Tests

```bash
# Rust tests
cargo test

# Python tests (when added)
pytest apps/agent-service/tests
```

## Environment Variables

Create `.env` file in project root:

```env
# Gemini 3 API
GEMINI_API_KEY=your-api-key-here

# Kernel
KERNEL_PORT=3000
KERNEL_LOG_LEVEL=debug

# Agents
AGENT_PORT=8000
AGENT_LOG_LEVEL=info

# Database
POSTGRES_URL=postgresql://raro:raro@postgres:5432/raro
REDIS_URL=redis://redis:6379

# Development
DEV_MODE=true
```

## Architecture Decisions

### Why Rust for the Kernel?

- **Type Safety**: DAG validation at compile time
- **Concurrency**: Tokio handles 1000s of WebSocket connections
- **Performance**: Low-latency streaming to frontend
- **Reliability**: Process isolation prevents agent crashes from affecting UI

### Why SvelteKit for UI?

- **Reactivity**: Svelte Runes (5.0) compile away virtual DOM overhead
- **State Management**: Built-in store system is simpler than React context
- **Animation**: Scoped CSS + transitions for smooth graph interactions
- **Bundle Size**: Smaller final bundle than React

### Why Python for Agents?

- **Ecosystem**: Gemini SDK, LangGraph, LangChain all in Python
- **Iteration**: Faster development cycles
- **Integration**: Easy to add new tools and models
- **Isolation**: Separate service prevents AI failures from cascading

## Performance Targets

| Metric | Target |
|--------|--------|
| WebSocket latency | <100ms |
| Kernel DAG validation | <50ms |
| Agent invocation | <2s (Gemini API dependent) |
| Cache hit rate | >90% with context caching |
| Concurrent workflows | 1000+ |

## Monitoring & Observability

- **Tracing**: Distributed traces with OpenTelemetry
- **Metrics**: Prometheus scrape endpoints
- **Logs**: Structured logging to stdout (container-friendly)
- **Health**: `/health` endpoints on all services

## Contributing

1. **Clone and set up**: Follow "Local Development" section
2. **Create feature branch**: `git checkout -b feature/my-feature`
3. **Make changes** and test locally
4. **Submit PR** with description of changes

## License

MIT

## Support

For questions or issues:
1. Check `/guidance/` for architectural decisions
2. Review API reference above
3. Open an issue on GitHub

---

**Built for the Gemini 3 Developer Competition Hackathon**

*"Kubernetes for Cognitive Workflows"* — Transform multi-agent AI from black box to transparent, reconfigurable platform.
