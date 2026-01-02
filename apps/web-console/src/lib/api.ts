// [[RARO]]/apps/web-console/src/lib/api.ts
// Purpose: Centralized API client for communicating with the Rust Kernel.
// Architecture: Interface Layer
// Dependencies: None

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:3000';

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
  // Handle protocol switching (ws/wss) automatically
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  // If API_BASE is absolute (http://...), extract host. Otherwise assume relative to current window.
  let host = window.location.host;
  
  try {
    const url = new URL(API_BASE);
    host = url.host;
  } catch (e) {
    // API_BASE might be relative path, fallback to window.location.host is fine
  }

  return `${protocol}//${host}/ws/runtime/${runId}`;
}