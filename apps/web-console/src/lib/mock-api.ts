// apps/web-console/src/lib/mock-api.ts
import { type WorkflowConfig } from './api';

// --- Types needed for simulation ---
type SimulationStep = {
    delay: number;
    state: {
        status: string;
        active_agents: string[];
        completed_agents: string[];
        failed_agents: string[];
        total_tokens_used: number;
        invocations: Array<{
            id: string;
            agent_id: string;
            status: 'success' | 'failed';
            tokens_used: number;
            latency_ms: number;
            artifact_id?: string;
            error_message?: string;
        }>;
    };
    signatures?: Record<string, string>;
};

// --- Mock Data Generators ---

const MOCK_ARTIFACTS: Record<string, any> = {
    'n1': { 
        result: `## Strategic Orchestration Plan: Latency Regression Analysis

Analysis of the user request indicates a high-complexity multi-step research process is required. To ensure accuracy and isolate the root cause of the "Gemini 3" performance regression, the agent will execute the following orchestration plan.

### Phase 1: Contextual Retrieval & Data Gathering
*Objective: Establish a baseline of expected behavior versus reported anomalies.*

1.  **Vector Search Execution**: Query the internal documentation database for 'Gemini 3 Architecture', specifically targeting the "MoE Routing" and "Sparse Attention" modules.
    *   *Rationale*: Recent changelogs indicate a shift in the expert selection logic which often correlates with tail latency spikes.
2.  **Benchmark Cross-Reference**: Retrieve the 'H1 2024 Performance Benchmarks' dataset. We need to identify if this is a global regression or isolated to specific prompt topologies (e.g., long-context vs. short-burst).

### Phase 2: Quantitative Verification
*Objective: Prove the regression exists using hard data.*

3.  **Log Ingestion**: Load the \`latency_metrics_2024\` dataset (n=10,000 samples) into the secure Python sandbox.
4.  **Statistical Profiling**: 
    *   Calculate P50, P95, and P99 latency.
    *   Perform a variance analysis on the 'pre-fill' phase vs. the 'generation' phase.
    *   **Hypothesis**: If pre-fill is stable but generation varies, the issue lies in the KV-cache quantization.

### Phase 3: Synthesis & Executive Reporting
*Objective: Deliver actionable insights.*

5.  **Correlation Mapping**: Map the architectural changes found in Phase 1 to the timestamps of the spikes found in Phase 2.
6.  **Final Output**: Generate a Markdown-formatted root-cause analysis report suitable for the Engineering Leadership team.`
    },

    'n2': { 
        result: `### Retrieval Execution Log
**Query Strategy**: Hybrid Search (Dense Vector + Keyword Sparse)
**Embedding Model**: \`text-embedding-004\`
**Top K**: 15 (Filtered to Top 5)

---

#### 1. [DOC-882] Sparse Activation Patterns in Large Context Windows
> **Relevance Score**: 0.94
> **Source**: /wiki/architecture/gemini-3/moe-routing
>
> **Snippet**: "...introduces a dynamic routing gate that skips up to 40% of parameters during idle context states. While this reduces compute by 30%, it introduces a 'cold-start' penalty when the context shifts rapidly between topics. This effectively hardens the model against long-context drifting but creates micro-stutters in diverse-topic batches..."

#### 2. [DOC-104] Tensor Processing Unit v5 Optimization Guide
> **Relevance Score**: 0.89
> **Source**: /eng/hardware/tpu-v5/memory-alignment
>
> **Snippet**: "Optimizing for Gemini 3 requires strictly typed memory mapping. Failure to align memory pages results in a 12% cache miss rate during expert switching. **Critical**: The new 'Flash-Lite' variant defaults to aggressive page-swapping, which can cause P99 latency spikes if the host memory is fragmented."

#### 3. [DOC-339] The RARO Orchestrator Whitepaper
> **Relevance Score**: 0.82
> **Source**: /internal/whitepapers/raro-system
>
> **Snippet**: "...integration allows for iterative reasoning. The orchestrator maintains a global state separate from the model's context window. This decoupling allows for 'Thought Loops' where the model can pause generation to retrieve fresh data without polluting the KV-cache."

#### 4. [DOC-401] Legacy Architecture Comparison (Gemini 1.5 vs 3.0)
> **Relevance Score**: 0.65
> **Source**: /marketing/comparison-sheets
>
> **Snippet**: "Compared to Gemini 1.5, the new sparsely gated mechanism reduces inference costs by approximately 30%. However, developers migrating from 1.5 Pro may notice a difference in 'Time to First Token' (TTFT) due to the Just-In-Time (JIT) loading of expert weights."

#### 5. [DOC-112] Security Posture for MoE Models
> **Relevance Score**: 0.61
> **Source**: /security/compliance/data-leakage
>
> **Snippet**: "Ensuring that expert routes do not leak private data remains a top priority. The 'Secure Enclave' routing layer adds an overhead of ~5ms per token but guarantees ISO-27001 compliance for enterprise workloads."`
    },

    'n3': { 
        result: "```python\nimport pandas as pd\n\n# Loading dataset with low_memory=False to prevent dtype warnings\ndata = load_dataset('latency_metrics_2024')\n\n# Calculate specific variance\nvar = data['response_time'].var()\nmean = data['response_time'].mean()\n\nprint(f'{var=}, {mean=}')\n```\n\n**Output:**\n`var=0.04231`\n`mean=145.2ms`" 
    },

    'n4': { 
        result: `# Final Report: RARO System Analysis & Gemini 3 Integration

## 1. Executive Summary
The integration of Gemini 3 Pro with the RARO (Reasoning-Acting-Retrieval-Orchestration) system has concluded with significant performance improvements. By leveraging the new sparse activation architecture, we have observed a **40% reduction in token wastage** and a marked improvement in logical consistency over long-running tasks.

However, the analysis also uncovered a trade-off regarding P99 latency during high-variance context switching, identified as a "Cold Expert" phenomenon.

## 2. Key Performance Metrics

We conducted a comprehensive A/B test comparing the legacy monolith architecture against the new MoE (Mixture of Experts) setup.

| Metric | Legacy System (v2.5) | Gemini 3 + RARO | Improvement |
| :--- | :--- | :--- | :--- |
| **P50 Latency** | 120ms | 95ms | **20% Faster** |
| **P99 Latency** | 450ms | 210ms | **53% Faster** |
| **Hallucination Rate** | 2.4% | <0.1% | **Significant** |
| **Context Window** | 32k | 100k+ | **3x Capacity** |
| **Cost per 1k Tokens** | $0.03 | $0.018 | **40% Cheaper** |

## 3. Root Cause Analysis: The "Cold Expert" Spike

During Phase 2 quantitative analysis, we observed sporadic latency spikes. Correlating this with [DOC-882] (Retrieved in Phase 1), we have confirmed:

1.  **Mechanism**: The dynamic routing gate skips 40% of parameters.
2.  **Trigger**: When a prompt shifts topics rapidly (e.g., Coding -> Creative Writing -> Math), the model must load previously "cold" experts into high-bandwidth memory.
3.  **Impact**: This results in a 12-15ms stutter per token for the first 5 tokens of the new sequence.

### 3.1 Mitigation Strategy
We successfully tested a pre-warming strategy using the RARO orchestrator's predictive capabilities:

> *Recommendation*: The Orchestrator should inject a hidden "priming" token when it detects a tool-use switch, effectively loading the necessary experts 200ms before the actual generation begins.

## 4. Architectural Deep Dive

### 4.1 Retrieval Precision
The RARO orchestrator utilizes a two-stage retrieval process. Initially, a broad vector search retrieves candidate documents. Subsequently, a cross-encoder reranks these documents, resulting in a precision score of **0.92** (up from 0.78).

### 4.2 Code Execution Safety
One of the most critical findings was the stability of the sandboxed execution environment. Throughout the stress testing phase, the system executed over 5,000 Python snippets. The error rate dropped to near zero, primarily due to the model's self-correction capabilities when encountering \`ImportErrors\` or \`SyntaxErrors\`.

## 5. Final Recommendations

Based on these findings, we recommend:
1.  **Immediate Deployment** to the production environment for "Chat" and "Analysis" workloads.
2.  **Delayed Deployment** for "Real-time Voice" workloads until the *Cold Expert* mitigation patch is applied.
3.  **Cost Savings**: The migration is projected to save the department approximately $12k/month in inference costs.`
    }
};

export async function mockStartRun(config: WorkflowConfig): Promise<{ success: boolean; run_id: string }> {
    console.log('[MOCK] Starting run with config:', config);
    return new Promise((resolve) => {
        setTimeout(() => {
            resolve({
                success: true,
                run_id: `mock-run-${Date.now()}`
            });
        }, 500);
    });
}

export async function mockGetArtifact(runId: string, agentId: string): Promise<any> {
    console.log(`[MOCK] Fetching artifact for ${agentId}`);
    return new Promise((resolve) => {
        setTimeout(() => {
            resolve(MOCK_ARTIFACTS[agentId] || { text: "No artifact data found." });
        }, 800); // Simulate network latency
    });
}

// --- Mock WebSocket Class ---

export class MockWebSocket {
    url: string;
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: ((err: any) => void) | null = null;
    
    private steps: SimulationStep[] = [];
    private currentStep = 0;
    private timer: any;

    constructor(url: string) {
        this.url = url;
        this.generateSimulationScenario();
        
        // Simulate connection delay
        setTimeout(() => {
            if (this.onopen) this.onopen();
            this.runSimulation();
        }, 500);
    }

    send(data: any) {
        console.log('[MOCK WS] Received:', data);
    }

    close() {
        console.log('[MOCK WS] Closing connection');
        clearTimeout(this.timer);
        if (this.onclose) this.onclose();
    }

    private generateSimulationScenario() {
        // SCENARIO: Linear success path matching the initialNodes in stores.ts
        // n1 (Orchestrator) -> n2 (Retrieval) & n3 (Code) -> n4 (Synthesis)

        const baseState = {
            status: 'RUNNING',
            active_agents: [],
            completed_agents: [],
            failed_agents: [],
            total_tokens_used: 0,
            invocations: []
        };

        this.steps = [
            // T+0: Start
            { 
                delay: 500, 
                state: { ...baseState, active_agents: ['n1'] } 
            },
            // T+2s: Orchestrator finishes, triggers workers
            { 
                delay: 2000, 
                state: { 
                    ...baseState, 
                    active_agents: ['n2', 'n3'], 
                    completed_agents: ['n1'],
                    total_tokens_used: 450,
                    invocations: [{ id: 'inv-1', agent_id: 'n1', status: 'success', tokens_used: 450, latency_ms: 1200, artifact_id: 'art-n1' }]
                },
                signatures: { 'n1': 'hash_xc92' }
            },
            // T+4s: Retrieval finishes first
            { 
                delay: 2000, 
                state: { 
                    ...baseState, 
                    active_agents: ['n3'], 
                    completed_agents: ['n1', 'n2'],
                    total_tokens_used: 1200,
                    invocations: [{ id: 'inv-2', agent_id: 'n2', status: 'success', tokens_used: 750, latency_ms: 800, artifact_id: 'art-n2' }]
                },
                signatures: { 'n2': 'hash_bm25' }
            },
            // T+3s: Code finishes
            { 
                delay: 3000, 
                state: { 
                    ...baseState, 
                    active_agents: ['n4'], 
                    completed_agents: ['n1', 'n2', 'n3'],
                    total_tokens_used: 2100,
                    invocations: [{ id: 'inv-3', agent_id: 'n3', status: 'success', tokens_used: 900, latency_ms: 2500, artifact_id: 'art-n3' }]
                },
                signatures: { 'n3': 'hash_py39' }
            },
            // T+5s: Synthesis finishes (Deep Think takes longer)
            { 
                delay: 5000, 
                state: { 
                    ...baseState, 
                    status: 'COMPLETED',
                    active_agents: [], 
                    completed_agents: ['n1', 'n2', 'n3', 'n4'],
                    total_tokens_used: 5400,
                    invocations: [{ id: 'inv-4', agent_id: 'n4', status: 'success', tokens_used: 3300, latency_ms: 4800, artifact_id: 'art-n4' }]
                },
                signatures: { 'n4': 'hash_fin1' }
            }
        ];
    }

    private runSimulation() {
        // Updated logic: If we are past the steps, close the connection exactly like the real API
        if (this.currentStep >= this.steps.length) {
            this.close();
            return;
        }

        const step = this.steps[this.currentStep];
        
        this.timer = setTimeout(() => {
            const message = {
                type: 'state_update',
                state: step.state,
                signatures: step.signatures || {}
            };

            if (this.onmessage) {
                this.onmessage({ data: JSON.stringify(message) });
            }

            this.currentStep++;
            this.runSimulation();
        }, step.delay);
    }
}