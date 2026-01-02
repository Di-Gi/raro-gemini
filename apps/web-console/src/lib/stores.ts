// [[RARO]]/apps/web-console/src/lib/stores.ts

import { writable, get } from 'svelte/store';
import { getWebSocketURL, USE_MOCK } from './api'; // Import USE_MOCK
import { MockWebSocket } from './mock-api';        // Import Mock Class

// === TYPES ===
export interface LogEntry {
  id: string
  timestamp: string;
  role: string;
  message: string;
  metadata?: string;
  isAnimated?: boolean;
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
  active: boolean;    // True = Animated Flow (Processing)
  finalized: boolean; // True = Solid Line (Completed)
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
  { id: 'n2', label: 'RETRIEVAL', x: 50, y: 30, model: 'gemini-2.5-flash', prompt: 'Search knowledge base for relevant context.', status: 'idle', role: 'worker' },
  { id: 'n3', label: 'CODE_INTERP', x: 50, y: 70, model: 'gemini-2.5-flash', prompt: 'Execute Python analysis on provided data.', status: 'idle', role: 'worker' },
  { id: 'n4', label: 'SYNTHESIS', x: 80, y: 50, model: 'GEMINI-3-DEEP-THINK', prompt: 'Synthesize all findings into a final report.', status: 'idle', role: 'worker' }
];

export const agentNodes = writable<AgentNode[]>(initialNodes);

// Initial Edges State
const initialEdges: PipelineEdge[] = [
  { from: 'n1', to: 'n2', active: false, finalized: false, pulseAnimation: false },
  { from: 'n1', to: 'n3', active: false, finalized: false, pulseAnimation: false },
  { from: 'n2', to: 'n4', active: false, finalized: false, pulseAnimation: false },
  { from: 'n3', to: 'n4', active: false, finalized: false, pulseAnimation: false }
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

export function addLog(role: string, message: string, metadata: string = '', isAnimated: boolean = false, customId?: string) {
  logs.update(l => {
    if (customId && l.find(entry => entry.id === customId)) {
      return l;
    }
    return [...l, {
      id: customId || crypto.randomUUID(),
      timestamp: new Date().toISOString(),
      role,
      message,
      metadata,
      isAnimated
    }];
  });
}

export function updateLog(id: string, updates: Partial<LogEntry>) {
  logs.update(l => 
    l.map(entry => entry.id === id ? { ...entry, ...updates } : entry)
  );
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

// Change type to union to allow MockSocket
let ws: WebSocket | MockWebSocket | null = null;

export function connectRuntimeWebSocket(runId: string) {
  if (ws) {
    ws.close();
  }

  const url = getWebSocketURL(runId);

  // ** MOCK SWITCHING **
  if (USE_MOCK) {
    addLog('SYSTEM', 'Initializing MOCK runtime environment...', 'DEBUG');
    ws = new MockWebSocket(url);
  } else {
    ws = new WebSocket(url);
  }

  // TypeScript note: MockWebSocket and WebSocket need matching signatures 
  // for the methods we use below. Since we defined them similarly in mock-api, this works.
  
  ws.onopen = () => {
    addLog('KERNEL', `Connected to runtime stream: ${runId}`, 'NET_OK');
    runtimeStore.set({ status: 'RUNNING', runId });
  };

  ws.onmessage = (event: any) => { // Use 'any' or generic event type
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'state_update' && data.state) {
        syncState(data.state, data.signatures);
        
        if (data.state.status) {
             runtimeStore.update(s => ({ ...s, status: data.state.status.toUpperCase() }));
        }
      } else if (data.error) {
        addLog('KERNEL', `Runtime error: ${data.error}`, 'ERR');
      }
    } catch (e) {
      console.error('Failed to parse WS message', e);
    }
  };

  ws.onclose = () => {
    addLog('KERNEL', 'Connection closed.', 'NET_END');
    
    // 1. Force Global Status to COMPLETED (if not failed)
    runtimeStore.update(s => {
        if (s.status !== 'FAILED') return { ...s, status: 'COMPLETED' };
        return s;
    });

    // 2. Force Finalize Edges
    pipelineEdges.update(edges => {
      return edges.map(e => ({
        ...e,
        active: false,
        pulseAnimation: false,
        finalized: e.active || e.finalized 
      }));
    });
  };

  if (!USE_MOCK) {
      (ws as WebSocket).onerror = (e) => {
        addLog('KERNEL', 'WebSocket connection error.', 'ERR');
      };
  }
}

// === STATE SYNCHRONIZATION LOGIC ===

const processedInvocations = new Set<string>();

function syncState(state: any, signatures: Record<string, string> = {}) {
  // Normalize status to handle lowercase from Rust serialization
  const rawStatus = state.status ? state.status.toLowerCase() : 'running';
  const isRunComplete = rawStatus === 'completed' || rawStatus === 'failed';

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

  // 2. Sync Edges
  pipelineEdges.update(edges => {
    return edges.map(e => {
      const fromComplete = state.completed_agents.includes(e.from);
      const toStarted = state.active_agents.includes(e.to) || state.completed_agents.includes(e.to);
      
      const hasDataFlowed = fromComplete && toStarted;
      
      // Active: Flowing but not done
      const active = hasDataFlowed && !isRunComplete;
      
      // Finalized: Flowed and now done
      const finalized = hasDataFlowed && isRunComplete;

      const sig = signatures[e.from];

      return {
        ...e,
        active,
        finalized,
        pulseAnimation: state.active_agents.includes(e.to),
        signatureHash: sig
      };
    });
  });

  // 3. Sync Telemetry
  const cost = (state.total_tokens_used / 1_000_000) * 2.0;
  telemetry.set({
    latency: 0,
    cacheHitRate: 0,
    totalCost: cost,
    errorCount: state.failed_agents.length,
    tokensUsed: state.total_tokens_used
  });

  // 4. Sync Logs
  if (state.invocations && Array.isArray(state.invocations)) {
    state.invocations.forEach(async (inv: any) => {
      if (!inv || !inv.id || processedInvocations.has(inv.id)) return;

      processedInvocations.add(inv.id);
      const agentLabel = (inv.agent_id || 'UNKNOWN').toUpperCase();

      try {
        if (inv.status === 'success') {
          if (inv.artifact_id) {
            addLog(agentLabel, 'Initiating output retrieval...', 'LOADING', false, inv.id);
            try {
              const { getArtifact } = await import('./api');
              const fetchPromise = getArtifact(state.run_id, inv.agent_id);
              const timeoutPromise = new Promise((_, reject) => setTimeout(() => reject(new Error('Timeout')), 5000));
              const artifact: any = await Promise.race([fetchPromise, timeoutPromise]);

              if (artifact) {
                let outputText = 'Output received';
                if (typeof artifact === 'string') outputText = artifact;
                else if (typeof artifact === 'object') {
                   if (artifact.result) outputText = artifact.result;
                   else if (artifact.output) outputText = artifact.output;
                   else if (artifact.text) outputText = artifact.text;
                   else outputText = JSON.stringify(artifact, null, 2);
                }
                updateLog(inv.id, {
                  message: outputText,
                  metadata: `TOKENS: ${inv.tokens_used || 0} | LATENCY: ${inv.latency_ms || 0}ms`,
                  isAnimated: true 
                });
              } else {
                updateLog(inv.id, { message: 'Artifact empty or expired', metadata: 'WARN' });
              }
            } catch (err) {
              console.error('Artifact fetch failed:', err);
              updateLog(inv.id, { message: 'Output retrieval failed. Check connection.', metadata: 'NET_ERR' });
            }
          } else {
            addLog(agentLabel, 'Completed (No Output)', `TOKENS: ${inv.tokens_used}`, false, inv.id);
          }
        } else if (inv.status === 'failed') {
          let errorDisplay = 'Execution Failed';
          if (inv.error_message) {
            errorDisplay = `<div style="color:#d32f2f; font-weight:bold; margin-bottom:4px">EXECUTION HALTED</div><div style="background: rgba(211, 47, 47, 0.05); border-left: 3px solid #d32f2f; padding: 8px; font-family: monospace; font-size: 11px; white-space: pre-wrap; color: #b71c1c;">${escapeHtml(inv.error_message)}</div>`;
          }
          addLog(agentLabel, errorDisplay, 'ERR', false, inv.id);
        }
      } catch (e) {
        console.error('Error processing invocation log:', e);
      }
    });
  }
}

function escapeHtml(unsafe: string) {
    return unsafe.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}