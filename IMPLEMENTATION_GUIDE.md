# RARO Implementation Guide: From Core Infrastructure to Gemini 3 Integration

This guide walks through implementing the full RARO system and integrating with Gemini 3 API.

## What's Complete

✅ **Core Infrastructure**:
- Rust kernel with DAG scheduler (Axum + Tokio)
- Python agent service skeleton (FastAPI)
- SvelteKit web console with interactive UI
- Docker Compose for local development
- Type-safe models and APIs

❌ **Still Needed**:
- Gemini 3 API integration
- Thought signature handling
- Multi-modal processing (PDF, video, images)
- Demo research workflows

## Phase 1: Gemini 3 Agent Implementation (Hours 1-8)

### Step 1: Set Up Gemini 3 SDK

Install the Google Generative AI SDK:

```bash
cd apps/agent-service
pip install google-generativeai
```

### Step 2: Implement Basic Agent Invocation

Edit `apps/agent-service/src/main.py` and replace the mock `invoke_agent` function:

```python
import google.generativeai as genai
from typing import Optional

# Configure Gemini 3
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

@app.post("/invoke", response_model=AgentResponse)
async def invoke_agent(request: AgentRequest):
    """
    Invoke a Gemini 3 agent with the given request
    """
    logger.info(f"Invoking agent {request.agent_id} with model {request.model}")

    try:
        # Map model names to Gemini 3 model IDs
        model_map = {
            "gemini-3-flash": "gemini-3-flash",
            "gemini-3-pro": "gemini-3-pro",
            "gemini-3-deep-think": "gemini-3-deep-think"
        }

        model_id = model_map.get(request.model, "gemini-3-pro")
        model = genai.GenerativeModel(model_id)

        # Build message with thought signature if available
        messages = []
        if request.thought_signature:
            logger.info(f"Using thought signature: {request.thought_signature}")
            # TODO: Include signature in request to preserve reasoning context

        # Add the main prompt with input data
        prompt = f"{request.prompt}\n\nInput Data:\n{json.dumps(request.input_data)}"

        # Invoke the model
        response = model.generate_content(prompt)

        # Extract thought signature (if available in response)
        thought_signature = None
        if hasattr(response, 'thought_signature'):
            thought_signature = response.thought_signature

        return AgentResponse(
            agent_id=request.agent_id,
            success=True,
            output={
                "text": response.text,
                "status": "completed"
            },
            tokens_used=response.usage_metadata.total_token_count if response.usage_metadata else 0,
            thought_signature=thought_signature
        )

    except Exception as e:
        logger.error(f"Error invoking agent: {str(e)}")
        return AgentResponse(
            agent_id=request.agent_id,
            success=False,
            error=str(e),
            tokens_used=0
        )
```

### Step 3: Implement Structured Output with Pydantic

Add structured output support for consistent agent responses:

```python
from pydantic import BaseModel
from typing import Any

class ResearchOutput(BaseModel):
    """Structured output for research synthesis"""
    summary: str
    key_findings: list[str]
    methodology: Optional[str] = None
    confidence_score: float

@app.post("/invoke/structured")
async def invoke_agent_structured(request: AgentRequest):
    """
    Invoke an agent and enforce structured output
    """
    # Use Gemini 3 structured output mode with JSON schema
    model = genai.GenerativeModel(request.model)

    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "key_findings": {"type": "array", "items": {"type": "string"}},
            "methodology": {"type": "string"},
            "confidence_score": {"type": "number"}
        }
    }

    response = model.generate_content(request.prompt)
    # TODO: Parse response to schema and validate
```

## Phase 2: Thought Signature Handling (Hours 8-12)

### Understanding Thought Signatures

Gemini 3's thought signatures preserve reasoning context across API calls:

```
Request 1 (Agent A):
  → Gemini processes with reasoning
  → Returns: output + thought_signature

Request 2 (Agent B):
  → Include: previous thought_signature
  → Gemini continues reasoning from Agent A
  → Returns: output + new_thought_signature
```

### Implementation

Edit `apps/agent-service/src/main.py`:

```python
@app.post("/invoke")
async def invoke_agent(request: AgentRequest):
    model = genai.GenerativeModel(request.model)

    # Build the content with thought signature
    contents = []

    if request.thought_signature:
        # TODO: Add thought signature to request
        # Note: This depends on Gemini API implementation
        pass

    # Add the user message
    contents.append({
        "role": "user",
        "parts": [request.prompt]
    })

    response = model.generate_content(contents)

    # Extract signature for next agent
    thought_sig = None
    if hasattr(response, '_thought_signature'):
        thought_sig = response._thought_signature

    return AgentResponse(
        agent_id=request.agent_id,
        success=True,
        output={"text": response.text},
        tokens_used=response.usage_metadata.total_token_count,
        thought_signature=thought_sig
    )
```

### Storing Signatures in Kernel

Edit `apps/kernel-server/src/runtime.rs`:

```rust
pub fn set_thought_signature(
    &self,
    run_id: &str,
    agent_id: &str,
    signature: String
) -> Result<(), String> {
    let mut store = self
        .thought_signatures
        .get_mut(run_id)
        .ok_or_else(|| "Run not found".to_string())?;

    store.signatures.insert(agent_id.to_string(), signature);
    // Also cache in Redis for persistence
    // TODO: Add Redis integration
    Ok(())
}
```

## Phase 3: Multi-Modal Processing (Hours 12-20)

### Phase 3a: PDF Extraction Agent

```python
# apps/agent-service/agents/pdf_extractor.py

from PyPDF2 import PdfReader
import base64

async def extract_pdf(file_path: str, request: AgentRequest):
    """Extract text and metadata from PDF"""

    with open(file_path, 'rb') as f:
        pdf_reader = PdfReader(f)

    # Extract text from all pages
    text_content = ""
    for page in pdf_reader.pages:
        text_content += page.extract_text()

    # Use Gemini to analyze and structure
    model = genai.GenerativeModel(request.model)

    prompt = f"""Analyze this research paper and extract:
    1. Title
    2. Authors
    3. Abstract
    4. Methodology
    5. Key findings
    6. Limitations

    Paper content:
    {text_content[:10000]}..."""  # Limit tokens

    response = model.generate_content(prompt)

    return {
        "extracted": response.text,
        "page_count": len(pdf_reader.pages),
        "word_count": len(text_content.split())
    }
```

### Phase 3b: Video Analysis Agent

```python
# apps/agent-service/agents/video_analyzer.py

import base64

async def analyze_video(video_path: str, request: AgentRequest):
    """Analyze video content with Gemini 3 vision"""

    # For hackathon: extract frames and analyze with Gemini
    # Production would use Gemini's native video support

    import cv2

    cap = cv2.VideoCapture(video_path)
    frames = []

    # Extract key frames (every 5 seconds)
    frame_count = 0
    while cap.isOpened() and len(frames) < 10:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_count % 150 == 0:  # ~5 seconds at 30fps
            _, buffer = cv2.imencode('.jpg', frame)
            frames.append(base64.b64encode(buffer).decode())

        frame_count += 1

    # Analyze frames with Gemini
    model = genai.GenerativeModel("gemini-3-pro")

    prompt = f"Analyze these {len(frames)} video frames and summarize the key content"

    image_parts = [
        {
            "mime_type": "image/jpeg",
            "data": frame
        }
        for frame in frames[:5]  # Limit to 5 frames
    ]

    response = model.generate_content([prompt] + image_parts)

    return {
        "analysis": response.text,
        "frames_analyzed": len(frames),
        "video_duration_s": cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS)
    }
```

### Phase 3c: Knowledge Graph Builder

```python
# apps/agent-service/agents/kg_builder.py

async def build_knowledge_graph(documents: list, request: AgentRequest):
    """Build a knowledge graph from multiple documents"""

    model = genai.GenerativeModel("gemini-3-deep-think")

    combined_content = "\n\n---\n\n".join(
        [f"Document {i+1}:\n{doc}" for i, doc in enumerate(documents)]
    )

    prompt = f"""Analyze these research documents and create a knowledge graph in JSON format:

    {{
        "entities": [
            {{"id": "entity1", "type": "concept", "name": "...", "description": "..."}},
            ...
        ],
        "relationships": [
            {{"source": "entity1", "target": "entity2", "type": "related_to", "weight": 0.9}},
            ...
        ],
        "gaps": ["What research gaps are identified?"],
        "novel_hypotheses": ["What new hypotheses could be tested?"]
    }}

    Documents:
    {combined_content}"""

    response = model.generate_content(prompt)

    import json
    try:
        kg = json.loads(response.text)
    except json.JSONDecodeError:
        kg = {"error": "Failed to parse knowledge graph"}

    return kg
```

## Phase 4: Demo Research Workflow (Hours 20-24)

### Create Research Workflow Config

```python
# workflows/research_synthesis.json

{
  "id": "research_synthesis_v1",
  "name": "Multi-Paper Research Synthesis",
  "agents": [
    {
      "id": "orchestrator",
      "role": "orchestrator",
      "model": "gemini-3-pro",
      "tools": ["plan_research", "delegate_tasks"],
      "prompt": "You are the orchestrator. Analyze the research goal and delegate to specialized agents.",
      "depends_on": []
    },
    {
      "id": "pdf_extractor",
      "role": "worker",
      "model": "gemini-3-flash",
      "tools": ["extract_pdf", "parse_figures"],
      "prompt": "Extract structured information from research papers.",
      "depends_on": ["orchestrator"]
    },
    {
      "id": "video_analyzer",
      "role": "worker",
      "model": "gemini-3-pro",
      "tools": ["analyze_video"],
      "prompt": "Analyze author presentations and extract insights.",
      "depends_on": ["orchestrator"]
    },
    {
      "id": "kg_builder",
      "role": "worker",
      "model": "gemini-3-deep-think",
      "tools": ["build_knowledge_graph", "cross_reference"],
      "prompt": "Build a comprehensive knowledge graph connecting all documents.",
      "depends_on": ["pdf_extractor", "video_analyzer"]
    },
    {
      "id": "hypothesis_generator",
      "role": "worker",
      "model": "gemini-3-deep-think",
      "tools": ["generate_hypothesis", "identify_gaps"],
      "prompt": "Generate novel research hypotheses based on the knowledge graph.",
      "depends_on": ["kg_builder"]
    },
    {
      "id": "synthesizer",
      "role": "worker",
      "model": "gemini-3-pro",
      "tools": ["create_report", "format_output"],
      "prompt": "Synthesize all findings into a comprehensive research report.",
      "depends_on": ["hypothesis_generator", "kg_builder"]
    }
  ],
  "max_token_budget": 1000000,
  "timeout_ms": 300000
}
```

### Frontend: Upload Research Materials

Add to `apps/web-console/src/components/UploadPanel.svelte`:

```svelte
<script lang="ts">
  import { addLog } from '$lib/stores'

  let files: FileList

  async function uploadAndStart() {
    // Upload files to agent service
    const formData = new FormData()
    Array.from(files).forEach(f => formData.append('files', f))

    const response = await fetch('/api/upload', {
      method: 'POST',
      body: formData
    })

    const { workflow_id } = await response.json()

    // Start workflow
    const start_response = await fetch('/api/runtime/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        workflow_id,
        workflow_config: {...}
      })
    })

    const { run_id } = await start_response.json()
    addLog('SYSTEM', `Started workflow ${run_id}`)
  }
</script>

<div class="upload-panel">
  <h3>Upload Research Materials</h3>
  <input type="file" multiple bind:files />
  <button on:click={uploadAndStart}>Start Synthesis</button>
</div>
```

## Phase 5: Testing & Iteration

### Unit Tests

```bash
# Rust tests
cargo test --all

# Python tests
pytest apps/agent-service/tests/test_agents.py
```

### Integration Testing

Create `e2e_test.sh`:

```bash
#!/bin/bash

# Start services
docker-compose up -d

# Wait for services
sleep 5

# Test workflow
curl -X POST http://localhost:3000/runtime/start \
  -H "Content-Type: application/json" \
  -d @workflows/research_synthesis.json

# Check status
curl http://localhost:3000/runtime/state?run_id=test123
```

## Performance Optimization

### Context Caching

For repeated analyses on similar documents:

```python
# Store cached embeddings
cache_key = hashlib.sha256(document_text.encode()).hexdigest()
if cache_key in redis_cache:
    return redis_cache[cache_key]
```

### Parallel Agent Execution

Agents with same dependencies execute in parallel:

```rust
// In kernel runtime
let mut parallel_tasks = vec![];
for agent in agents_ready_to_run {
    parallel_tasks.push(tokio::spawn(invoke_agent(agent)));
}

futures::future::join_all(parallel_tasks).await
```

## Deployment

### Docker Build

```bash
docker-compose build
docker-compose up -d
```

### GCP Cloud Run

```bash
# Build and push images
gcloud builds submit --tag gcr.io/PROJECT/raro-kernel apps/kernel-server
gcloud builds submit --tag gcr.io/PROJECT/raro-agents apps/agent-service
gcloud builds submit --tag gcr.io/PROJECT/raro-web apps/web-console

# Deploy
gcloud run deploy raro-kernel --image gcr.io/PROJECT/raro-kernel
gcloud run deploy raro-agents --image gcr.io/PROJECT/raro-agents
gcloud run deploy raro-web --image gcr.io/PROJECT/raro-web
```

## Troubleshooting

### Kernel Won't Start

```bash
# Check logs
docker-compose logs kernel

# Verify Rust compilation
cd apps/kernel-server
cargo check
```

### Agent Service Errors

```bash
# Check Python syntax
python -m py_compile src/main.py

# Test Gemini API key
python -c "import google.generativeai; google.generativeai.configure(api_key='YOUR_KEY')"
```

### UI Not Connecting

```bash
# Check WebSocket connection
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" http://localhost:3000
```

## Next Steps

1. ✅ Complete Gemini 3 integration
2. ✅ Implement all agent types
3. ✅ Create demo workflow
4. ✅ Record demo video
5. ✅ Polish UI/UX
6. ✅ Write documentation
7. ✅ Submit to DevPost

---

**Current Status**: Core infrastructure complete. Ready for Gemini 3 integration.

**Estimated Completion**: 24-36 hours with full team
