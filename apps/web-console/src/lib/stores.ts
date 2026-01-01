import { writable } from 'svelte/store';

// === TYPES ===
export interface LogEntry {
  timestamp: string;
  role: string;
  message: string;
  metadata?: string;
}

export interface AgentNode {
  id: string;
  label: string;
  x: number;  // Position as percentage (0-100)
  y: number;  // Position as percentage (0-100)
  model: string;
  prompt: string;
  status: 'idle' | 'running' | 'complete' | 'failed';
}

// Edges connect agent nodes in the pipeline visualization
export interface PipelineEdge {
  from: string;  // Source node ID
  to: string;    // Target node ID
  active: boolean;  // Whether data is flowing through this edge
  pulseAnimation: boolean;  // Whether to show pulse animation
  signatureHash?: string;  // Optional signature hash for data verification
}

export interface RuntimeState {
  status: 'IDLE' | 'RUNNING' | 'COMPLETED' | 'FAILED';
  runId: string | null;
}

// === LOGGING STORE ===
export const logs = writable<LogEntry[]>([]);

export function addLog(role: string, message: string, metadata: string = '') {
  logs.update(l => [...l, {
    timestamp: new Date().toISOString(),
    role,
    message,
    metadata
  }]);
}

// === RUNTIME STORE ===
export const runtimeStore = writable<RuntimeState>({
  status: 'IDLE',
  runId: null
});

// === AGENT NODES STORE ===
const initialNodes: AgentNode[] = [
  { id: 'n1', label: 'ORCHESTRATOR', x: 20, y: 50, model: 'GEMINI-3-PRO', prompt: 'Determine optimal sub-tasks.', status: 'idle' },
  { id: 'n2', label: 'RETRIEVAL', x: 50, y: 30, model: 'GEMINI-3-FLASH', prompt: 'Query vector database.', status: 'idle' },
  { id: 'n3', label: 'CODE_INTERP', x: 50, y: 70, model: 'GEMINI-3-FLASH', prompt: 'Execute Python analysis.', status: 'idle' },
  { id: 'n4', label: 'SYNTHESIS', x: 80, y: 50, model: 'GEMINI-3-DEEP-THINK', prompt: 'Compile final report.', status: 'idle' }
];

export const agentNodes = writable<AgentNode[]>(initialNodes);

export function updateNodeStatus(id: string, status: 'idle' | 'running' | 'complete' | 'failed') {
  agentNodes.update(nodes =>
    nodes.map(n => n.id === id ? { ...n, status } : n)
  );
}

// === PIPELINE EDGES STORE ===
const initialEdges: PipelineEdge[] = [
  { from: 'n1', to: 'n2', active: false, pulseAnimation: false },
  { from: 'n1', to: 'n3', active: false, pulseAnimation: false },
  { from: 'n2', to: 'n4', active: false, pulseAnimation: false },
  { from: 'n3', to: 'n4', active: false, pulseAnimation: false }
];

export const pipelineEdges = writable<PipelineEdge[]>(initialEdges);

// === SELECTION STORE ===
export const selectedNode = writable<string | null>(null);

export function selectNode(id: string) {
  selectedNode.set(id);
}

export function deselectNode() {
  selectedNode.set(null);
}