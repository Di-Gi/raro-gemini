// [[RARO]]/apps/web-console/src/lib/stores.ts
// Purpose: Reactive state management. Handles WebSocket connections and maps Kernel state to UI state.
// Architecture: State Management Layer
// Dependencies: Svelte Store, API

import { writable, get } from 'svelte/store';
import { getWebSocketURL } from './api';

// === TYPES ===
export interface LogEntry {
  id: string
  timestamp: string;
  role: string;
  message: string;
  metadata?: string;
}

export interface AgentNode {
  id: string;
  label: string;
  x: number;
  y: number;
  model: string;
  prompt: string;
  status: 'idle' | 'running' | 'complete' | 'failed';
  role: 'orchestrator' | 'worker' | 'observer';
}

export interface PipelineEdge {
  from: string;
  to: string;
  active: boolean;
  pulseAnimation: boolean;
  signatureHash?: string;
}

export interface TelemetryState {
  latency: number;
  cacheHitRate: number;
  totalCost: number;
  errorCount: number;
  tokensUsed: number;
}

// === STORES ===
export const logs = writable<LogEntry[]>([]);
export const runtimeStore = writable<{ status: string; runId: string | null }>({
  status: 'IDLE',
  runId: null
});

// Initial Nodes State
const initialNodes: AgentNode[] = [
  { id: 'n1', label: 'ORCHESTRATOR', x: 20, y: 50, model: 'GEMINI-3-PRO', prompt: 'Analyze the user request and determine optimal sub-tasks.', status: 'idle', role: 'orchestrator' },
  { id: 'n2', label: 'RETRIEVAL', x: 50, y: 30, model: 'GEMINI-3-FLASH-PREVIEW', prompt: 'Search knowledge base for relevant context.', status: 'idle', role: 'worker' },
  { id: 'n3', label: 'CODE_INTERP', x: 50, y: 70, model: 'GEMINI-3-FLASH-PREVIEW', prompt: 'Execute Python analysis on provided data.', status: 'idle', role: 'worker' },
  { id: 'n4', label: 'SYNTHESIS', x: 80, y: 50, model: 'GEMINI-3-DEEP-THINK', prompt: 'Synthesize all findings into a final report.', status: 'idle', role: 'worker' }
];

export const agentNodes = writable<AgentNode[]>(initialNodes);

// Initial Edges State
const initialEdges: PipelineEdge[] = [
  { from: 'n1', to: 'n2', active: false, pulseAnimation: false },
  { from: 'n1', to: 'n3', active: false, pulseAnimation: false },
  { from: 'n2', to: 'n4', active: false, pulseAnimation: false },
  { from: 'n3', to: 'n4', active: false, pulseAnimation: false }
];

export const pipelineEdges = writable<PipelineEdge[]>(initialEdges);
export const selectedNode = writable<string | null>(null);

// Telemetry Store
export const telemetry = writable<TelemetryState>({
  latency: 0,
  cacheHitRate: 0,
  totalCost: 0,
  errorCount: 0,
  tokensUsed: 0
});

// === ACTIONS ===

export function addLog(role: string, message: string, metadata: string = '') {
  logs.update(l => [...l, {
    id: crypto.randomUUID(), // <--- GENERATE UNIQUE ID
    timestamp: new Date().toISOString(),
    role,
    message,
    metadata
  }]);
}

export function updateNodeStatus(id: string, status: 'idle' | 'running' | 'complete' | 'failed') {
  agentNodes.update(nodes =>
    nodes.map(n => n.id === id ? { ...n, status } : n)
  );
}

export function selectNode(id: string) {
  selectedNode.set(id);
}

export function deselectNode() {
  selectedNode.set(null);
}

// === WEBSOCKET HANDLING ===

let ws: WebSocket | null = null;

export function connectRuntimeWebSocket(runId: string) {
  if (ws) {
    ws.close();
  }

  const url = getWebSocketURL(runId);
  ws = new WebSocket(url);

  ws.onopen = () => {
    addLog('KERNEL', `Connected to runtime stream: ${runId}`, 'NET_OK');
    runtimeStore.set({ status: 'RUNNING', runId });
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'state_update' && data.state) {
        syncState(data.state, data.signatures);
      } else if (data.error) {
        addLog('KERNEL', `Runtime error: ${data.error}`, 'ERR');
      }
    } catch (e) {
      console.error('Failed to parse WS message', e);
    }
  };

  ws.onclose = () => {
    addLog('KERNEL', 'Connection closed.', 'NET_END');
    runtimeStore.update(s => ({ ...s, status: 'COMPLETED' }));
  };

  ws.onerror = (e) => {
    addLog('KERNEL', 'WebSocket connection error.', 'ERR');
  };
}

// === STATE SYNCHRONIZATION LOGIC ===

function syncState(state: any, signatures: Record<string, string> = {}) {
  // 1. Sync Node Status
  agentNodes.update(nodes => {
    return nodes.map(n => {
      let status: 'idle' | 'running' | 'complete' | 'failed' = 'idle';
      if (state.active_agents.includes(n.id)) status = 'running';
      else if (state.completed_agents.includes(n.id)) status = 'complete';
      else if (state.failed_agents.includes(n.id)) status = 'failed';
      return { ...n, status };
    });
  });

  // 2. Sync Edges (Animate based on flow)
  // Logic: If 'from' is complete and 'to' is running/complete, active=true
  pipelineEdges.update(edges => {
    return edges.map(e => {
      const fromComplete = state.completed_agents.includes(e.from);
      const toStarted = state.active_agents.includes(e.to) || state.completed_agents.includes(e.to);
      
      const active = fromComplete && toStarted;
      
      // Check if signature exists for the source
      const sig = signatures[e.from];

      return {
        ...e,
        active,
        pulseAnimation: state.active_agents.includes(e.to), // Pulse if target is currently thinking
        signatureHash: sig
      };
    });
  });

  // 3. Sync Telemetry
  // Simplistic cost model: $2 per 1M tokens (average)
  const cost = (state.total_tokens_used / 1_000_000) * 2.0;
  
  telemetry.set({
    latency: 0, // Calculated client-side or passed if available
    cacheHitRate: 0, // Need support in Kernel state
    totalCost: cost,
    errorCount: state.failed_agents.length,
    tokensUsed: state.total_tokens_used
  });

  // 4. Sync Logs (Invocations)
  // Check specifically for new invocations we haven't logged yet would be ideal,
  // but for now, we just log major state transitions if not already handled.
  // (Actual log streaming from kernel would be better, but we simulate it via status changes for now)
}