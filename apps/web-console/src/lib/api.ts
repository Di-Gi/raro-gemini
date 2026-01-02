// [[RARO]]/apps/web-console/src/lib/api.ts
import { mockStartRun, mockGetArtifact } from './mock-api';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:3000';

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
    const res = await fetch(`${API_BASE}/runtime/start`, {
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
  if (USE_MOCK) return `mock://runtime/${runId}`; // Placeholder for mock

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  let host = window.location.host;

  try {
    const url = new URL(API_BASE);
    host = url.host;
  } catch (e) {
    // API_BASE might be relative path
  }

  return `${protocol}//${host}/ws/runtime/${runId}`;
}

export async function getArtifact(runId: string, agentId: string): Promise<any> {
  // ** MOCK INTERCEPTION **
  if (USE_MOCK) {
    return mockGetArtifact(runId, agentId);
  }

  try {
    const res = await fetch(`${API_BASE}/runtime/${runId}/artifact/${agentId}`);

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