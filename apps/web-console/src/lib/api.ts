// [[RARO]]/apps/web-console/src/lib/api.ts
import { mockStartRun, mockGetArtifact } from './mock-api';

const KERNEL_API = import.meta.env.VITE_KERNEL_URL || '/api';
const AGENT_API = import.meta.env.VITE_AGENT_URL || '/agent-api';

// ** NEW DEBUG FLAG **
export const USE_MOCK = import.meta.env.VITE_USE_MOCK_API === 'true';

export interface WorkflowConfig {
  id: string;
  name: string;
  agents: AgentConfig[];
  max_token_budget: number;
  timeout_ms: number;
}

export interface AgentConfig {
  id: string;
  role: 'orchestrator' | 'worker' | 'observer';
  model: string;
  tools: string[];
  input_schema: any;
  output_schema: any;
  cache_policy: string;
  depends_on: string[];
  prompt: string;
  position?: { x: number; y: number };
}

export async function startRun(config: WorkflowConfig): Promise<{ success: boolean; run_id: string }> {
  // ** MOCK INTERCEPTION **
  if (USE_MOCK) {
    return mockStartRun(config);
  }

  try {
    const res = await fetch(`${KERNEL_API}/runtime/start`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(config),
    });

    if (!res.ok) {
      throw new Error(`API Error: ${res.statusText}`);
    }

    return await res.json();
  } catch (e) {
    console.error('Failed to start run:', e);
    throw e;
  }
}

export function getWebSocketURL(runId: string): string {
  if (USE_MOCK) return `mock://runtime/${runId}`;

  // In development with Vite proxy, use relative WebSocket path
  // Vite will proxy ws://localhost:5173/ws/runtime/{id} → ws://kernel:3000/ws/runtime/{id}
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host; // localhost:5173 in dev, actual host in prod

  return `${protocol}//${host}/ws/runtime/${runId}`;
}

export async function getArtifact(runId: string, agentId: string): Promise<any> {
  // ** MOCK INTERCEPTION **
  if (USE_MOCK) {
    return mockGetArtifact(runId, agentId);
  }

  try {
    const res = await fetch(`${KERNEL_API}/runtime/${runId}/artifact/${agentId}`);

    if (res.status === 404) {
      console.warn(`Artifact not found for agent ${agentId}`);
      return null;
    }

    if (!res.ok) {
      throw new Error(`Failed to fetch artifact: ${res.status} ${res.statusText}`);
    }

    return await res.json();
  } catch (e) {
    console.error('Artifact fetch error:', e);
    throw e;
  }
}

export async function generateWorkflowPlan(userQuery: string): Promise<WorkflowConfig> {
    if (USE_MOCK) {
        // Mock Architect behavior
        return {
            id: `plan-${Date.now()}`,
            name: 'Mock_Architecture_Plan',
            agents: [
                {
                    id: 'mock_researcher',
                    role: 'worker',
                    model: 'fast',
                    tools: ['web_search'],
                    input_schema: {},
                    output_schema: {},
                    cache_policy: 'ephemeral',
                    depends_on: [],
                    prompt: `Research request: ${userQuery}`,
                    position: { x: 30, y: 50 }
                },
                {
                    id: 'mock_synthesizer',
                    role: 'worker',
                    model: 'reasoning',
                    tools: [],
                    input_schema: {},
                    output_schema: {},
                    cache_policy: 'ephemeral',
                    depends_on: ['mock_researcher'],
                    prompt: 'Synthesize findings',
                    position: { x: 70, y: 50 }
                }
            ],
            max_token_budget: 50000,
            timeout_ms: 60000
        };
    }

    try {
        const res = await fetch(`${AGENT_API}/plan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: userQuery })
        });

        if (!res.ok) throw new Error(`Architect Error: ${res.statusText}`);
        
        const manifest = await res.json();
        
        // Enrich logic: The Python manifest might lack UI positions.
        // We add basic layouting here if missing.
        const enrichedAgents = manifest.agents.map((agent: any, index: number) => ({
            ...agent,
            // Simple diagonal layout if missing (PipelineStage handles display logic mostly)
            position: agent.position || { x: 20 + (index * 15), y: 30 + (index * 10) },
            // Ensure Rust-required fields exist
            input_schema: agent.input_schema || {},
            output_schema: agent.output_schema || {},
            cache_policy: 'ephemeral' 
        }));

        return {
            ...manifest,
            // Ensure ID exists
            id: manifest.id || `flow-${Date.now()}`,
            max_token_budget: 100000,
            timeout_ms: 60000,
            agents: enrichedAgents
        };

    } catch (e) {
        console.error('Plan generation failed:', e);
        throw e;
    }
}