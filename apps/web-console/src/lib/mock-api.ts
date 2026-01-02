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
    'n1': { result: "Analysis of user request indicates a multi-step research process is required. \n\n1. Retrieve historical data.\n2. Execute quantitative analysis.\n3. Synthesize findings." },
    'n2': { result: "Found 14 relevant papers in the vector database regarding 'Gemini 3 Architecture'. Key concepts: MoE (Mixture of Experts), sparse activation, and long-context hardening." },
    'n3': { result: "```python\nimport pandas as pd\n\ndata = load_dataset('latency_metrics')\nprint(data.describe())\n```\n\ncomputed_variance: 0.042\nmean_response: 145ms" },
    'n4': { result: "# Final Report: RARO System Analysis\n\nThe integration of Gemini 3 Pro with the RARO orchestrator has demonstrated a 40% reduction in token wastage.\n\n### Key Findings\n- **Retrieval**: High precision (0.92)\n- **Execution**: Zero hallucination in code blocks\n- **Synthesis**: Coherent logic flow maintained across 100k context window." }
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
        if (this.currentStep >= this.steps.length) return;

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