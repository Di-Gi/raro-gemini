# 🎭 Debug Puppet - Mock Injection Service

A debugging tool for intercepting and mocking agent responses in the RARO system. This allows you to test how the system propagates responses, handles tool calls, and processes delegations without relying on LLM behavior.

## Architecture

The Puppet Master uses Redis as a "mailbox" system:

1. **Web UI** (localhost:8082) → Sends mock payload to `/inject` endpoint
2. **Debug Puppet Service** → Stores payload in Redis with key `mock:{run_id}:{agent_id}`
3. **Agent Service** → Before calling Gemini, checks Redis for mock
   - **If mock exists**: Uses mock response, deletes key, bypasses LLM
   - **If no mock**: Proceeds with normal Gemini call

## Access

- **UI Dashboard**: http://localhost:8082
- **API Endpoint**: http://localhost:8082/inject

## Usage

### Basic Injection Flow

1. **Start a workflow** from the Web Console
2. **Identify target agent** you want to mock (e.g., `retrieval`, `orchestrator`)
3. **Open Puppet Master** at http://localhost:8082
4. **Fill in the form**:
   - **Run ID**: The active workflow run ID
   - **Agent ID**: The specific agent to intercept (e.g., `web_searcher`)
   - **Content**: The exact text output you want to inject
5. **Click "Inject Payload"**
6. **Wait for execution**: When the Kernel activates that agent, your mock will be used instead of calling Gemini

### Snippet Templates

The UI provides quick templates for common test scenarios:

#### 1. Delegation Test
Tests dynamic graph splicing by forcing an agent to request delegation:

```markdown
I need to delegate this task.
```json:delegation
{
  "reason": "Testing delegation system",
  "strategy": "child",
  "new_nodes": [
    {
      "id": "test_worker",
      "role": "worker",
      "model": "fast",
      "prompt": "Complete the delegated task",
      "tools": ["read_file"],
      "depends_on": []
    }
  ]
}
\`\`\`
```

#### 2. File Write Test
Forces a specific file to be written (tests artifact promotion):

```markdown
Saving test report.
```json:function
{
  "name": "write_file",
  "args": {
    "filename": "test_report.txt",
    "content": "# Test Report\\n\\nStatus: SUCCESS"
  }
}
\`\`\`
```

#### 3. Web Search Test
Simulates web search tool execution:

```markdown
Searching for information.
```json:function
{
  "name": "web_search",
  "args": {
    "query": "RARO framework architecture"
  }
}
\`\`\`
```

#### 4. Simple Text Test
Just injects plain text to verify downstream context propagation:

```
Task completed successfully. All requirements have been met.
```

## Use Cases

### 1. Testing Topology Changes
Inject `json:delegation` blocks to verify the Kernel correctly splices the DAG graph.

### 2. Testing Context Propagation
Mock an upstream agent's output to verify downstream agents receive the correct context through Redis.

### 3. Testing Tool Execution
Inject `json:function` blocks to force specific tool calls without LLM randomness.

### 4. Testing Error Handling
Inject malformed JSON or error messages to test system robustness.

### 5. Testing File Promotion
Mock file generation to verify the Kernel's artifact promotion system works correctly.

## API Reference

### POST /inject

Arm a mock response for a specific agent in a specific run.

**Request Body**:
```json
{
  "run_id": "uuid-of-workflow-run",
  "agent_id": "orchestrator",
  "content": "I have completed the task successfully.",
  "force_tool_execution": true
}
```

**Response**:
```json
{
  "status": "armed",
  "key": "mock:uuid:orchestrator",
  "record": { ... }
}
```

### GET /injections

List all injection history.

### POST /clear

Clear injection history.

### DELETE /mock/{run_id}/{agent_id}

Disarm a pending mock (remove from Redis before it triggers).

### GET /status

Health check and status information.

## Important Notes

- **One-time use**: Mocks are deleted from Redis after first use
- **TTL**: Mocks expire after 10 minutes if unused
- **Single-shot execution**: Mock is applied on the first turn, and if tools are executed, the agent **terminates immediately**
  - This prevents the real LLM from being called with tool results
  - Your mock response IS the final response
  - No autonomous follow-up from the LLM after tool execution
- **Tool execution**: If your mock contains `json:function` blocks, the tools WILL actually execute
- **Delegation**: If your mock contains `json:delegation` blocks, the graph WILL be modified
- **Deterministic testing**: Puppet mode ensures your injected mock is the ONLY response - no LLM "leakage"

## Docker Configuration

Defined in `docker-compose.yml`:

```yaml
debug-puppet:
  ports:
    - "8082:8081"
  environment:
    - REDIS_HOST=redis
    - REDIS_PORT=6379
  depends_on:
    - redis
```

## Development

Local development without Docker:

```bash
cd apps/debug-puppet
pip install -r requirements.txt
export REDIS_HOST=localhost
export REDIS_PORT=6379
uvicorn main:app --host 0.0.0.0 --port 8081 --reload
```

Access at http://localhost:8081

## Security Warning

⚠️ **This is a debugging tool for development only.**

- Do not expose in production
- Allows arbitrary code execution through tool calls
- No authentication or authorization
- Can modify workflow topology dynamically

## See Also

- [Debug Probe](../debug-probe/README.md) - Passive prompt inspector
- [Agent Service](../agent-service/README.md) - Where interception happens
- [Kernel Server](../kernel-server/README.md) - DAG orchestration
