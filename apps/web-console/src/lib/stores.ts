// [[RARO]]/apps/web-console/src/lib/stores.ts

import { writable, get } from 'svelte/store';
import { getWebSocketURL, USE_MOCK, type WorkflowConfig } from './api'; // Import USE_MOCK
import { MockWebSocket, mockResumeRun, mockStopRun } from './mock-api'; 
import { DagLayoutEngine } from './layout-engine'; // Import Layout Engine

// Import KERNEL_API for resume/stop endpoints
const KERNEL_API = import.meta.env.VITE_KERNEL_URL || '/api';

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
  acceptsDirective: boolean;  // Can this node receive operator directives?
  allowDelegation: boolean;   // Can this node spawn sub-agents?
}

export interface PipelineEdge {
  from: string;
  to: string;
  active: boolean;    // True = Animated Flow (Processing)
  finalized: boolean; // True = Solid Line (Completed)
  pulseAnimation: boolean;
  signatureHash?: string;
}

interface TopologySnapshot {
    nodes: string[];
    edges: { from: string; to: string }[];
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

// === THEME STORE ===
export type ThemeMode = 'ARCHIVAL' | 'PHOSPHOR';
export const themeStore = writable<ThemeMode>('ARCHIVAL');

export function toggleTheme() {
    themeStore.update(current => current === 'ARCHIVAL' ? 'PHOSPHOR' : 'ARCHIVAL');
}

// === RFS STORES ===
// The list of all files available in /storage/library
export const libraryFiles = writable<string[]>([]);

// The subset of files currently linked to the active directive
export const attachedFiles = writable<string[]>([]);

// Helper to toggle attachment status
export function toggleAttachment(fileName: string) {
    attachedFiles.update(files => {
        if (files.includes(fileName)) {
            return files.filter(f => f !== fileName);
        } else {
            return [...files, fileName];
        }
    });
}

// Initial Nodes State
const initialNodes: AgentNode[] = [
  { id: 'orchestrator', label: 'ORCHESTRATOR', x: 20, y: 50, model: 'reasoning', prompt: 'Analyze the user request and determine optimal sub-tasks.', status: 'idle', role: 'orchestrator', acceptsDirective: true, allowDelegation: true },
  { id: 'retrieval', label: 'RETRIEVAL', x: 50, y: 30, model: 'fast', prompt: 'Search knowledge base for relevant context.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
  { id: 'code_interpreter', label: 'CODE_INTERP', x: 50, y: 70, model: 'fast', prompt: 'Execute Python analysis on provided data.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
  { id: 'synthesis', label: 'SYNTHESIS', x: 80, y: 50, model: 'thinking', prompt: 'Synthesize all findings into a final report.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false }
];

export const agentNodes = writable<AgentNode[]>(initialNodes);

// Initial Edges State
const initialEdges: PipelineEdge[] = [
  { from: 'orchestrator', to: 'retrieval', active: false, finalized: false, pulseAnimation: false },
  { from: 'orchestrator', to: 'code_interpreter', active: false, finalized: false, pulseAnimation: false },
  { from: 'retrieval', to: 'synthesis', active: false, finalized: false, pulseAnimation: false },
  { from: 'code_interpreter', to: 'synthesis', active: false, finalized: false, pulseAnimation: false }
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

// === NEW STORE ===
// False = Execution Mode (Direct to Kernel)
// True = Architect Mode (Query -> Agent Service -> Update Graph)
export const planningMode = writable<boolean>(false);


// === ACTIONS ===
// === GRAPH MUTATION ACTIONS ===

export function updateNodePosition(id: string, x: number, y: number) {
    agentNodes.update(nodes => 
        nodes.map(n => n.id === id ? { ...n, x, y } : n)
    );
}

export function addConnection(from: string, to: string) {
    pipelineEdges.update(edges => {
        // Prevent duplicates
        if (edges.find(e => e.from === from && e.to === to)) return edges;
        // Prevent self-loops
        if (from === to) return edges;
        
        return [...edges, {
            from,
            to,
            active: false,
            finalized: false,
            pulseAnimation: false
        }];
    });
}

export function removeConnection(from: string, to: string) {
    pipelineEdges.update(edges => 
        edges.filter(e => !(e.from === from && e.to === to))
    );
}



export function createNode(x: number, y: number) {
    agentNodes.update(nodes => {
        const id = `node_${Date.now().toString().slice(-4)}`;
        return [...nodes, {
            id,
            label: 'NEW_AGENT',
            x,
            y,
            model: 'fast',
            prompt: 'Describe task...',
            status: 'idle',
            role: 'worker',
            acceptsDirective: false,  // Default to false for new nodes
            allowDelegation: false    // Default to false for new nodes
        }];
    });
}



export function deleteNode(id: string) {
    // 1. Remove Node
    agentNodes.update(nodes => nodes.filter(n => n.id !== id));
    
    // 2. Remove associated edges
    pipelineEdges.update(edges => edges.filter(e => e.from !== id && e.to !== id));
    
    // 3. Clear selection if needed
    if (get(selectedNode) === id) {
        selectedNode.set(null);
    }
}

export function renameNode(oldId: string, newId: string): boolean {
  // 1. Validation: Ensure new ID is unique and valid
  if (!newId || newId === oldId) return false;
  
  const currentNodes = get(agentNodes);
  if (currentNodes.find(n => n.id === newId)) {
    console.warn(`ID "${newId}" already exists.`);
    return false;
  }

  // 2. Update the Node definition
  agentNodes.update(nodes => 
    nodes.map(n => n.id === oldId ? { ...n, id: newId } : n)
  );

  // 3. Update all Edges (Rewiring)
  pipelineEdges.update(edges => 
    edges.map(e => ({
      ...e,
      from: e.from === oldId ? newId : e.from,
      to: e.to === oldId ? newId : e.to
    }))
  );

  // 4. Update Selection State (Keep the panel open)
  if (get(selectedNode) === oldId) {
    selectedNode.set(newId);
  }

  return true;
}


/**
 * PURE STATE MUTATION
 * Takes a backend manifest and paints it to the UI stores.
 * Does NOT trigger execution.
 */
export function loadWorkflowManifest(manifest: WorkflowConfig) {
  // 1. Transform Manifest Agents -> UI Nodes
  const newNodes: AgentNode[] = manifest.agents.map((agent, index) => {
    // Use semantic alias directly (fast, reasoning, thinking)
    // No normalization needed - backend already sends the correct alias
    return {
      id: agent.id,
      label: agent.id.replace(/^(agent_|node_)/i, '').toUpperCase().substring(0, 12),
      // Use provided position or fallback calculation
      x: agent.position?.x || (20 + (index * 15)),
      y: agent.position?.y || (30 + (index * 10)),
      model: agent.model,
      prompt: agent.prompt,
      status: 'idle',
      role: agent.role,
      acceptsDirective: agent.accepts_directive || agent.role === 'orchestrator',  // Use backend flag or default to true for orchestrators
      allowDelegation: agent.allow_delegation || false  // Use backend flag or default to false
    };
  });

  // 2. Transform Dependencies -> UI Edges
  const newEdges: PipelineEdge[] = [];
  manifest.agents.forEach(agent => {
    if (agent.depends_on) {
      agent.depends_on.forEach(parentId => {
        newEdges.push({
          from: parentId,
          to: agent.id,
          active: false,
          finalized: false,
          pulseAnimation: false
        });
      });
    }
  });

  // 3. Commit
  agentNodes.set(newNodes);
  pipelineEdges.set(newEdges);
  selectedNode.set(null); // Clear selection
}

/**
 * LOGIC GAP FIX: Flow A
 * Translates Backend Manifest -> Frontend State
 */
export function overwriteGraphFromManifest(manifest: WorkflowConfig) {
  // 1. Transform Manifest Agents -> UI Nodes
  const newNodes: AgentNode[] = manifest.agents.map((agent, index) => {
    // Use semantic alias directly (fast, reasoning, thinking)
    // No normalization needed - backend already sends the correct alias
    return {
      id: agent.id,
      label: agent.id.replace(/^(agent_|node_)/i, '').toUpperCase(), // Clean ID for display
      x: agent.position?.x || (20 + index * 15), // Fallback layout logic
      y: agent.position?.y || (30 + index * 10),
      model: agent.model,
      prompt: agent.prompt,
      status: 'idle',
      role: agent.role,
      acceptsDirective: agent.accepts_directive || agent.role === 'orchestrator',  // Use backend flag or default to true for orchestrators
      allowDelegation: agent.allow_delegation || false  // Use backend flag or default to false
    };
  });

  // 2. Transform Manifest Dependencies -> UI Edges
  const newEdges: PipelineEdge[] = [];
  manifest.agents.forEach(agent => {
    agent.depends_on.forEach(parentId => {
      newEdges.push({
        from: parentId,
        to: agent.id,
        active: false,
        finalized: false,
        pulseAnimation: false
      });
    });
  });

  // 3. Commit to Store
  agentNodes.set(newNodes);
  pipelineEdges.set(newEdges);
}


// HITL (Human-in-the-Loop) Actions
export async function resumeRun(runId: string) {
    if (USE_MOCK) {
        runtimeStore.update(s => ({ ...s, status: 'RUNNING' }));
        addLog('KERNEL', 'Mock: Resuming execution...', 'SYS');
        // CHANGE: Actually trigger the mock engine
        await mockResumeRun(runId);
        return;
    }

    try {
        const res = await fetch(`${KERNEL_API}/runtime/${runId}/resume`, { method: 'POST' });

        if (!res.ok) {
            throw new Error(`Resume failed: ${res.status} ${res.statusText}`);
        }

        addLog('KERNEL', 'Execution resumed by operator', 'SYS');
    } catch (e) {
        console.error('Resume API error:', e);
        addLog('KERNEL', `Resume failed: ${e}`, 'ERR');
    }
}

export async function stopRun(runId: string) {
    if (USE_MOCK) {
        runtimeStore.update(s => ({ ...s, status: 'FAILED' }));
        addLog('KERNEL', 'Mock: Run terminated by operator', 'SYS');
        // CHANGE: Actually trigger the mock engine
        await mockStopRun(runId);
        return;
    }

    try {
        const res = await fetch(`${KERNEL_API}/runtime/${runId}/stop`, { method: 'POST' });

        if (!res.ok) {
            throw new Error(`Stop failed: ${res.status} ${res.statusText}`);
        }

        addLog('KERNEL', 'Run terminated by operator', 'SYS');
    } catch (e) {
        console.error('Stop API error:', e);
        addLog('KERNEL', `Stop failed: ${e}`, 'ERR');
    }
}
// === AUTHORITATIVE TOPOLOGY SYNC ===
// This function trusts the Kernel's topology as the source of truth
function syncTopology(topology: TopologySnapshot) {
    const currentNodes = get(agentNodes);
    const currentEdges = get(pipelineEdges);

    // 1. Reconcile Edges (Source of Truth)
    // Rebuild the edge list based on Kernel topology to ensure we capture rewiring
    const newEdges: PipelineEdge[] = topology.edges.map(kEdge => {
        // Try to preserve animation state if edge already existed
        const existing = currentEdges.find(e => e.from === kEdge.from && e.to === kEdge.to);
        return {
            from: kEdge.from,
            to: kEdge.to,
            active: existing ? existing.active : false,
            finalized: existing ? existing.finalized : false,
            pulseAnimation: existing ? existing.pulseAnimation : false,
            signatureHash: existing ? existing.signatureHash : undefined
        };
    });

    // 2. Reconcile Nodes
    const nodeMap = new Map(currentNodes.map(n => [n.id, n]));
    const newNodes: AgentNode[] = [];
    let structureChanged = false;

    // Check for edge count mismatch or node count mismatch
    if (newEdges.length !== currentEdges.length || topology.nodes.length !== currentNodes.length) {
        structureChanged = true;
    }

    topology.nodes.forEach(nodeId => {
        if (nodeMap.has(nodeId)) {
            // Existing node: Keep it, preserve state
            newNodes.push(nodeMap.get(nodeId)!);
        } else {
            // NEW NODE DETECTED (Delegation)
            // Initialize at 0,0. The Layout Engine will move it immediately.
            structureChanged = true;
            newNodes.push({
                id: nodeId,
                // Heuristic Labeling since Kernel currently sends IDs only in topology
                label: nodeId.toUpperCase().substring(0, 12),
                x: 0,
                y: 0,
                model: 'fast', // Default to fast for dynamically spawned agents
                prompt: 'Dynamic Agent',
                status: 'running', // Usually spawned active
                role: 'worker',
                acceptsDirective: false,  // Dynamically spawned agents don't accept directives by default
                allowDelegation: false    // Dynamically spawned agents don't delegate by default
            });
        }
    });

    // 3. APPLY LAYOUT (Only if structure changed)
    if (structureChanged) {
        console.log('[UI] Topology mutation detected. Recalculating layout...');
        const layoutNodes = DagLayoutEngine.computeLayout(newNodes, newEdges);
        agentNodes.set(layoutNodes);
        pipelineEdges.set(newEdges);
    } else {
        // If structure is same, update edges to respect any strict rewiring
        pipelineEdges.set(newEdges);
    }
}

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
  console.log('[WS] Connecting to:', url);

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
    console.log('[WS] Connected successfully to:', url);
    addLog('KERNEL', `Connected to runtime stream: ${runId}`, 'NET_OK');
    runtimeStore.set({ status: 'RUNNING', runId });
  };

  ws.onmessage = (event: any) => { // Use 'any' or generic event type
    console.log('[WS] Message received:', event.data.substring(0, 200));
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'state_update' && data.state) {
        
        // === APPROVAL DETECTION ===
        const currentState = get(runtimeStore);
        // FIXED: Normalize to uppercase and check for underscore format
        const newStateStr = (data.state.status || '').toUpperCase();

        if (newStateStr === 'AWAITING_APPROVAL' && currentState.status !== 'AWAITING_APPROVAL') {
          // Check if we already logged this approval request to avoid duplicates
          const logsList = get(logs);
          const hasPending = logsList.some(l => l.metadata === 'INTERVENTION');

          if (!hasPending) {
            addLog(
              'CORTEX',
              'SAFETY_PATTERN_TRIGGERED',
              'INTERVENTION', // Metadata tag
              false,
              'approval-req-' + Date.now() // Custom ID
            );
          }
        }

        // CRITICAL FIX: Pass topology to syncState
        syncState(data.state, data.signatures, data.topology);

        if (data.state.status) {
             runtimeStore.update(s => ({ ...s, status: data.state.status.toUpperCase() }));
        }
      } else if (data.error) {
        addLog('KERNEL', `Runtime error: ${data.error}`, 'ERR');
      }
    } catch (e) {
      console.error('[WS] Failed to parse message:', e, event.data);
    }
  };

  ws.onclose = (e: CloseEvent) => {
    console.log('[WS] Connection closed:', e.code, e.reason);
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
        console.error('[WS] Error event:', e);
        addLog('KERNEL', 'WebSocket connection error.', 'ERR');
      };
  }
}

// === STATE SYNCHRONIZATION LOGIC ===

const processedInvocations = new Set<string>();

function syncState(state: any, signatures: Record<string, string> = {}, topology?: TopologySnapshot) {
    // 1. Sync Topology FIRST (Create/update nodes/edges from Kernel's authoritative view)
    if (topology) {
        syncTopology(topology);
    }

    // Normalize status to handle lowercase from Rust serialization
    const rawStatus = state.status ? state.status.toLowerCase() : 'running';
    const isRunComplete = rawStatus === 'completed' || rawStatus === 'failed';

    // 2. Sync Node Status
    agentNodes.update(nodes => {
        return nodes.map(n => {
            let status: 'idle' | 'running' | 'complete' | 'failed' = 'idle';
            if (state.active_agents.includes(n.id)) status = 'running';
            else if (state.completed_agents.includes(n.id)) status = 'complete';
            else if (state.failed_agents.includes(n.id)) status = 'failed';
            return { ...n, status };
        });
    });

    // 3. Sync Edges
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

    // 4. Sync Telemetry
    const cost = (state.total_tokens_used / 1_000_000) * 2.0;
    telemetry.set({
        latency: 0,
        cacheHitRate: 0,
        totalCost: cost,
        errorCount: state.failed_agents.length,
        tokensUsed: state.total_tokens_used
    });

    // 5. Sync Logs
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
                                let outputText = '';

                                if (typeof artifact === 'string') {
                                    outputText = artifact;
                                } else if (typeof artifact === 'object') {
                                    // 1. Try to find actual human-readable content
                                    if (artifact.result) outputText = artifact.result;
                                    else if (artifact.output) outputText = artifact.output;
                                    else if (artifact.text) outputText = artifact.text;
                                    
                                    // 2. Intercept Metadata-only objects (The issue you saw)
                                    // If we see 'artifact_stored' or 'model' but no text fields, 
                                    // suppress the raw JSON dump.
                                    else if ('artifact_stored' in artifact || 'model' in artifact) {
                                        // Leave empty; we will populate with file tag or generic success below
                                        outputText = ''; 
                                    }
                                    else {
                                        // Genuine unknown object, fallback to dump
                                        outputText = JSON.stringify(artifact, null, 2);
                                    }
                                }

                                // 3. Ensure File Generation Tags are present
                                // This is crucial for <ArtifactCard /> rendering
                                if (artifact.files_generated && Array.isArray(artifact.files_generated) && artifact.files_generated.length > 0) {
                                    const filename = artifact.files_generated[0];
                                    const systemTag = `[SYSTEM: Generated Image saved to '${filename}']`;

                                    // Only append if the tag isn't already present in the text
                                    if (!outputText.includes(systemTag)) {
                                        outputText = outputText ? `${outputText}\n\n${systemTag}` : systemTag;
                                    }
                                }

                                // 4. Final Safety Fallback
                                // If the Agent Service saved metadata to Redis but didn't save the text "result",
                                // and no files were generated, we show a polite status instead of raw JSON.
                                if (!outputText || outputText.trim() === '') {
                                    outputText = "Task execution completed successfully.";
                                }

                                updateLog(inv.id, {
                                    message: outputText,
                                    metadata: `TOKENS: ${inv.tokens_used || 0} | LATENCY: ${Math.round(inv.latency_ms || 0)}ms`, // Rounded latency
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