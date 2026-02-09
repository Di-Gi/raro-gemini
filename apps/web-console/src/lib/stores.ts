// [[RARO]]/apps/web-console/src/lib/stores.ts
// Change Type: Modified
// Purpose: Fix Ghost Card rendering and invocation state caching issues
// Architectural Context: State Management
// Dependencies: api, layout-engine, mock-api, templates

import { writable, get } from 'svelte/store';
import { getWebSocketURL, USE_MOCK, type WorkflowConfig } from './api'; // Import USE_MOCK
import { MockWebSocket, mockResumeRun, mockStopRun, activeSocket } from './mock-api'; 
import { DagLayoutEngine } from './layout-engine'; // Import Layout Engine
import { TEMPLATES, type GraphTemplate } from './templates'; // [[NEW]] Import Templates

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
  category?: string;  // NEW: For tool/reasoning categorization (TOOL_CALL, TOOL_RESULT, REASONING)
  // [[NEW]] Fields for merging Tool Results into the Call log
  toolResult?: string;      // The output text from the tool
  toolStatus?: 'success' | 'error'; 
  toolDuration?: number;    // Execution time in ms
  isComplete?: boolean;     // Has the tool finished?
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
  tools: string[];            // Active tools provisioned to this agent
}

export interface PipelineEdge {
  from: string;
  to: string;
  active: boolean;    // True = Animated Flow (Processing)
  finalized: boolean; // True = Solid Line (Completed)
  pulseAnimation: boolean;
  signatureHash?: string;
}

// === IDENTITY UTILITY HELPER (ROBUST) ===
// Made robust against casing and underscores
// Uses broader matching to prevent "Intent Crash" from naming variations
export function deriveToolsFromId(id: string): string[] {
    const tools = new Set<string>();
    const lowerId = id.toLowerCase();

    // 1. Base Identity Grants (Broader matching)
    // Research: "research_", "researcher", "web_searcher"
    if (lowerId.includes('research') || lowerId.includes('web')) {
        tools.add('web_search');
    }

    // Analysis/Code: "analyze_", "analyst", "coder", "python"
    if (lowerId.includes('analy') || lowerId.includes('code') || lowerId.includes('math')) {
        tools.add('execute_python');
    }

    // IO: "writer", "coder", "logger"
    if (lowerId.includes('writ') || lowerId.includes('code') || lowerId.includes('log')) {
        tools.add('write_file');
    }

    // 2. Master Grant (orchestrator or master)
    if (lowerId.startsWith('master') || lowerId.startsWith('orch')) {
        tools.add('web_search');
        tools.add('execute_python');
        tools.add('write_file');
    }

    return Array.from(tools);
}

// Updated to support enriched topology data from backend
interface TopologyNodeData {
    id: string;
    label?: string;
    prompt?: string;
    model?: string;
    role?: 'orchestrator' | 'worker' | 'observer';
    tools?: string[];
    accepts_directive?: boolean;
    allow_delegation?: boolean;
    x?: number;
    y?: number;
}

interface TopologySnapshot {
    nodes: (string | TopologyNodeData)[]; // Support both legacy strings and new rich objects
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
export const themeStore = writable<ThemeMode>('PHOSPHOR');

export function toggleTheme() {
    themeStore.update(current => current === 'ARCHIVAL' ? 'PHOSPHOR' : 'ARCHIVAL');
}

// === CALIBRATION STATE ===
export const calibrationActive = writable<boolean>(false);

export function checkCalibrationStatus() {
    // Check local storage for the flag
    if (typeof localStorage === 'undefined') return; // SSR safety

    const hasCalibrated = localStorage.getItem('raro_calibrated');

    // Check if we are in a fresh session (no logs, no run ID) to prevent
    // calibration triggering mid-workflow on a refresh if storage was cleared
    const isFreshState = get(runtimeStore).status === 'IDLE';

    if (!hasCalibrated && isFreshState) {
        calibrationActive.set(true);
    }
}

export function completeCalibration() {
    if (typeof localStorage !== 'undefined') {
        localStorage.setItem('raro_calibrated', 'true');
    }
    calibrationActive.set(false);
}

export function reinitializeConsole() {
    // Clear existing logs and restore initial boot messages
    logs.set([]);
    addLog('KERNEL', 'RARO Runtime Environment v0.1.0.', 'SYSTEM_BOOT');
    setTimeout(() => {
        addLog('SYSTEM', 'Connection established. Status: IDLE.', 'NET_OK');
    }, 300);
}

// === ENVIRONMENT REFRESH FUNCTIONS ===
// Centralized refresh logic for Library and Artifacts
// Used by both EnvironmentRail and ArtifactViewer

/**
 * Refresh library files and update store
 */
export async function refreshLibrary(): Promise<string[]> {
    try {
        const { getLibraryFiles } = await import('./api');
        const files = await getLibraryFiles();
        libraryFiles.set(files);
        return files;
    } catch (err) {
        console.error('[stores] Failed to refresh library:', err);
        throw err;
    }
}

/**
 * Refresh artifacts (returns data without updating a store)
 * Components can handle the data as needed
 */
export async function refreshArtifacts(): Promise<any[]> {
    try {
        const { getAllArtifacts } = await import('./api');
        const artifacts = await getAllArtifacts();
        return artifacts;
    } catch (err) {
        console.error('[stores] Failed to refresh artifacts:', err);
        throw err;
    }
}

/**
 * Refresh both library and artifacts
 */
export async function refreshAll(): Promise<{ files: string[]; artifacts: any[] }> {
    const [files, artifacts] = await Promise.all([
        refreshLibrary(),
        refreshArtifacts()
    ]);
    return { files, artifacts };
}

/**
 * Legacy function - kept for compatibility
 * @deprecated Use refreshAll() instead
 */
export async function refreshEnvironment() {
    try {
        const { files, artifacts } = await refreshAll();

        // Dispatch a custom event so non-store logic (like Artifact lists in Rail) can react
        window.dispatchEvent(new CustomEvent('raro:env_updated', {
            detail: { files, artifacts }
        }));
    } catch (e) {
        console.warn('[RARO] Silent refresh failed:', e);
    }
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

// === INITIALIZATION via TEMPLATE ===
// Default to STANDARD on load
// This replaces the previous hardcoded `initialNodes` and `initialEdges`
const DEFAULT_TEMPLATE = TEMPLATES.STANDARD;

// Initial Nodes State
export const agentNodes = writable<AgentNode[]>(DEFAULT_TEMPLATE.nodes);

// Initial Edges State
export const pipelineEdges = writable<PipelineEdge[]>(DEFAULT_TEMPLATE.edges);

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

// Graph Update Flash Indicator
export const graphFlash = writable<boolean>(false);

// Helper to trigger flash animation
function triggerGraphFlash() {
    graphFlash.set(true);
    setTimeout(() => graphFlash.set(false), 1000); // Flash for 1 second
}

function generateId() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return crypto.randomUUID();
    }
    // Fallback for non-HTTPS (raw IP) environments
    return Math.random().toString(36).substring(2) + Date.now().toString(36);
}

// === ACTIONS ===

// [[NEW]] Template Applicator
export function applyTemplate(templateKey: string) {
    const template = TEMPLATES[templateKey];
    if (!template) {
        console.warn(`Template ${templateKey} not found.`);
        return;
    }

    // Reset stores with template data
    // Use deep copy (JSON serialization) to prevent mutation of the static template definitions
    // This ensures that if the user modifies the graph, the original template remains pure.
    agentNodes.set(JSON.parse(JSON.stringify(template.nodes)));
    pipelineEdges.set(JSON.parse(JSON.stringify(template.edges)));

    // Clear selection to avoid stale state in configuration pane
    selectedNode.set(null);

    // Trigger flash animation
    triggerGraphFlash();
}

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
            allowDelegation: false,   // Default to false for new nodes
            tools: []                 // Start empty, user or ID will populate
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
      allowDelegation: agent.allow_delegation || false,  // Use backend flag or default to false
      tools: agent.tools || []  // Load tools from backend or default to empty
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

  // Trigger flash animation
  triggerGraphFlash();
}

/**
 * LOGIC GAP FIX: Flow A
 * Translates Backend Manifest -> Frontend State
 */
export function overwriteGraphFromManifest(manifest: WorkflowConfig) {
  loadWorkflowManifest(manifest);
}


// HITL (Human-in-the-Loop) Actions
export async function resumeRun(runId: string) {
    const isSim = USE_MOCK || runId.startsWith('sim-');

    if (isSim) {
        runtimeStore.update(s => ({ ...s, status: 'RUNNING' }));
        addLog('KERNEL', 'Resuming execution...', 'SYS');
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
    const isSim = USE_MOCK || runId.startsWith('sim-');

    if (isSim) {
        runtimeStore.update(s => ({ ...s, status: 'FAILED' }));
        addLog('KERNEL', 'Run terminated by operator', 'SYS');
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

    topology.nodes.forEach(nodeData => {
        // Check if it's an object (Rich data) or string (Legacy support)
        const nodeId = typeof nodeData === 'string' ? nodeData : nodeData.id;

        if (nodeMap.has(nodeId)) {
            // Existing node: Keep it, preserve state
            newNodes.push(nodeMap.get(nodeId)!);
        } else {
            // NEW NODE DETECTED (Delegation)
            // Initialize at 0,0 or use provided position. The Layout Engine will move it if needed.
            structureChanged = true;

            // If we have rich data from backend, use it; otherwise fall back to heuristics
            if (typeof nodeData !== 'string') {
                newNodes.push({
                    id: nodeData.id,
                    label: (nodeData.label || nodeData.id).toUpperCase().substring(0, 12),
                    x: nodeData.x || 0,
                    y: nodeData.y || 0,
                    model: nodeData.model || 'fast',
                    prompt: nodeData.prompt || 'Dynamic Agent',
                    status: 'running', // Usually spawned active
                    role: (nodeData.role as any) || 'worker',
                    acceptsDirective: nodeData.accepts_directive ?? false,
                    allowDelegation: nodeData.allow_delegation ?? false,
                    tools: nodeData.tools || deriveToolsFromId(nodeData.id)
                });
            } else {
                // Legacy string format - use heuristics
                newNodes.push({
                    id: nodeId,
                    label: nodeId.toUpperCase().substring(0, 12),
                    x: 0,
                    y: 0,
                    model: 'fast',
                    prompt: 'Dynamic Agent',
                    status: 'running',
                    role: 'worker',
                    acceptsDirective: false,
                    allowDelegation: false,
                    tools: deriveToolsFromId(nodeId)
                });
            }
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

/**
 * addLog: Enhanced to merge TOOL_RESULT into previous TOOL_CALL
 */
export function addLog(
    role: string,
    message: string,
    metadata: string = '',
    isAnimated: boolean = false,
    customId?: string,
    category?: string,
    extra?: any // Optional bag for duration, etc.
) {
  logs.update(currentLogs => {
    // 1. Check for Duplicate IDs
    if (customId && currentLogs.find(entry => entry.id === customId)) {
      return currentLogs;
    }

    // 2. MERGE STRATEGY: If this is a TOOL_RESULT, find the pending TOOL_CALL
    if (category === 'TOOL_RESULT') {
        // Search backwards for the most recent TOOL_CALL by this agent that isn't complete
        for (let i = currentLogs.length - 1; i >= 0; i--) {
            const entry = currentLogs[i];
            
            // Match Agent + Category + Pending Status
            if (entry.role === role && entry.category === 'TOOL_CALL' && !entry.isComplete) {
                // Return new array with specific entry updated
                const updatedLogs = [...currentLogs];
                updatedLogs[i] = {
                    ...entry,
                    isComplete: true,
                    toolResult: message, // The result message replaces/appends to the call
                    toolStatus: metadata === 'IO_ERR' ? 'error' : 'success',
                    metadata: metadata // Update metadata (IO_OK / IO_ERR)
                };
                return updatedLogs;
            }
        }
        // Fallback: If no matching call found (rare), insert as new entry below
    }

    // 3. Standard Insertion
    return [...currentLogs, {
      id: customId || generateId(),
      timestamp: new Date().toISOString(),
      role,
      message,
      metadata,
      isAnimated,
      category,
      // Initialize Tool Call state
      isComplete: category === 'TOOL_CALL' ? false : undefined
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

// FIX: Reset processing cache on new connection
const processedInvocations = new Set<string>();

export function connectRuntimeWebSocket(runId: string, manualMode: boolean = false, topology?: { nodes: string[]; edges: Array<{ from: string; to: string }> }) {
  if (ws) {
    ws.close();
  }

  // Clear processed invocations cache to prevent "Hanging" on simulated re-runs
  // If we don't do this, re-using IDs (common in mocks) causes the UI to ignore the new completion events
  processedInvocations.clear();

  const url = getWebSocketURL(runId);
  console.log('[WS] Connecting to:', url);

  // ** MOCK SWITCHING **
  if (USE_MOCK || manualMode) {
    addLog('SYSTEM', `Initializing MOCK environment (Manual: ${manualMode})...`, 'DEBUG');
    ws = new MockWebSocket(url, manualMode, topology);
  } else {
    ws = new WebSocket(url);
  }

  ws.onopen = () => {
    console.log('[WS] Connected successfully to:', url);
    addLog('KERNEL', `Connected to runtime stream: ${runId}`, 'NET_OK');
    runtimeStore.set({ status: 'RUNNING', runId });
  };

  ws.onmessage = (event: any) => { 
    console.log('[WS] Message received:', event.data.substring(0, 200));
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'state_update' && data.state) {
        
        // === APPROVAL DETECTION ===
        const currentState = get(runtimeStore);
        const newStateStr = (data.state.status || '').toUpperCase();

        if (newStateStr === 'AWAITING_APPROVAL' && currentState.status !== 'AWAITING_APPROVAL') {
          const logsList = get(logs);
          const hasPending = logsList.some(l => l.metadata === 'INTERVENTION');

          if (!hasPending) {
            addLog(
              'CORTEX',
              'SAFETY_PATTERN_TRIGGERED',
              'INTERVENTION', 
              false,
              'approval-req-' + Date.now()
            );
          }
        }

        syncState(data.state, data.signatures, data.topology);

        if (data.state.status) {
             runtimeStore.update(s => ({ ...s, status: data.state.status.toUpperCase() }));
        }
      }

      // ] Handle Real-Time Agent Events for Instant Feedback
      // This ensures the "Ghost Card" appears immediately, even if state_update is delayed
      else if (data.type === 'agent_started') {
          const agentId = data.agent_id;
          if (agentId) updateNodeStatus(agentId, 'running');
      }
      else if (data.type === 'agent_completed') {
          const agentId = data.agent_id;
          if (agentId) updateNodeStatus(agentId, 'complete');
      }
      else if (data.type === 'agent_failed') {
          const agentId = data.agent_id;
          if (agentId) updateNodeStatus(agentId, 'failed');
      }

      // Intermediate log events
      else if (data.type === 'log_event') {
        const agentId = data.agent_id ? data.agent_id.toUpperCase() : 'SYSTEM';
        const p = data.payload;

        addLog(
          agentId,
          p.message,
          p.metadata || 'INFO',
          false,
          undefined,
          p.category,
          p.extra 
        );
      }

      else if (data.error) {
        addLog('KERNEL', `Runtime error: ${data.error}`, 'ERR');
      }
    } catch (e) {
      console.error('[WS] Failed to parse message:', e, event.data);
    }
  };

  ws.onclose = (e: CloseEvent) => {
    console.log('[WS] Connection closed:', e.code, e.reason);
    addLog('KERNEL', 'Connection closed.', 'NET_END');
    
    runtimeStore.update(s => {
        if (s.status !== 'FAILED') return { ...s, status: 'COMPLETED' };
        return s;
    });

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

function syncState(state: any, signatures: Record<string, string> = {}, topology?: TopologySnapshot) {
    // 1. Sync Topology FIRST
    if (topology) {
        syncTopology(topology);
    }

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
            const active = hasDataFlowed && !isRunComplete;
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
        async function fetchAndPopulateArtifact(runId: string, inv: any) {
            const agentLabel = (inv.agent_id || 'UNKNOWN').toUpperCase();
            try {
                const { getArtifact } = await import('./api');
                const fetchPromise = getArtifact(runId, inv.agent_id);
                const timeoutPromise = new Promise((_, reject) => setTimeout(() => reject(new Error('Timeout')), 5000));
                const artifact: any = await Promise.race([fetchPromise, timeoutPromise]);

                if (artifact) {
                    let outputText = '';

                    if (typeof artifact === 'string') {
                        outputText = artifact;
                    } else if (typeof artifact === 'object') {
                        if (artifact.result) outputText = artifact.result;
                        else if (artifact.output) outputText = artifact.output;
                        else if (artifact.text) outputText = artifact.text;
                        else if ('artifact_stored' in artifact || 'model' in artifact) {
                            outputText = '';
                        }
                        else {
                            outputText = JSON.stringify(artifact, null, 2);
                        }
                    }

                    if (artifact.files_generated && Array.isArray(artifact.files_generated) && artifact.files_generated.length > 0) {
                        const filename = artifact.files_generated[0];
                        const isImage = /\.(png|jpg|jpeg|svg|webp)$/i.test(filename);
                        const label = isImage ? "Generated Image" : "Generated File";
                        const systemTag = `[SYSTEM: ${label} saved to '${filename}']`;

                        if (!outputText.includes(systemTag)) {
                            outputText = outputText ? `${outputText}\n\n${systemTag}` : systemTag;
                        }
                    }

                    if (!outputText || outputText.trim() === '') {
                        outputText = "Task execution completed successfully.";
                    }

                    updateLog(inv.id, {
                        message: outputText,
                        metadata: `TOKENS: ${inv.tokens_used || 0} | LATENCY: ${Math.round(inv.latency_ms || 0)}ms`,
                        isAnimated: true
                    });
                } else {
                    updateLog(inv.id, { message: 'Artifact empty or expired', metadata: 'WARN' });
                }
            } catch (err) {
                console.error('Artifact fetch failed:', err);
                updateLog(inv.id, { message: 'Output retrieval failed. Check connection.', metadata: 'NET_ERR' });
            }
        }

        state.invocations.forEach(async (inv: any) => {
            if (!inv || !inv.id || processedInvocations.has(inv.id)) return;

            processedInvocations.add(inv.id);
            const agentLabel = (inv.agent_id || 'UNKNOWN').toUpperCase();

            try {
                if (inv.status === 'success') {
                    if (inv.artifact_id) {
                        const currentLogs = get(logs);
                        const existingLiveLog = currentLogs.find(l =>
                            l.role === agentLabel &&
                            (l.category === 'REASONING' || l.category === 'TOOL_CALL')
                        );

                        if (existingLiveLog) {
                            await fetchAndPopulateArtifact(state.run_id, inv);
                        } else {
                            addLog(agentLabel, 'Initiating output retrieval...', 'LOADING', false, inv.id);
                            await fetchAndPopulateArtifact(state.run_id, inv);
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

// === SIMULATION CONTROL FUNCTIONS ===

let originalTopologySnapshot: {
    nodes: AgentNode[];
    edges: PipelineEdge[];
} | null = null;

function getCurrentTopology(): { nodes: string[]; edges: Array<{ from: string; to: string }> } {
    const nodes = get(agentNodes);
    const edges = get(pipelineEdges);

    return {
        nodes: nodes.map(n => n.id),
        edges: edges.map(e => ({ from: e.from, to: e.to }))
    };
}

function initSimulation() {
    const simId = `sim-${Date.now()}`;
    const topology = getCurrentTopology();

    originalTopologySnapshot = {
        nodes: JSON.parse(JSON.stringify(get(agentNodes))),
        edges: JSON.parse(JSON.stringify(get(pipelineEdges)))
    };

    logs.set([]);
    runtimeStore.set({ status: 'IDLE', runId: simId });

    connectRuntimeWebSocket(simId, true, topology);
    addLog('SIMULATOR', 'Debug session initialized with current pipeline topology.', 'READY');
}

export function stepSimulation() {
    if (!activeSocket) {
        initSimulation();
        setTimeout(() => {
            if (activeSocket) activeSocket.nextStep();
        }, 100);
    } else {
        activeSocket.nextStep();
    }
}

let autoRunTimer: any = null;

export function runSimulation() {
    if (!activeSocket) {
        initSimulation();
        setTimeout(() => autoStepLoop(), 100);
    } else {
        autoStepLoop();
    }
}

function autoStepLoop() {
    if (!activeSocket) return;

    activeSocket.nextStep();

    autoRunTimer = setTimeout(() => {
        const state = get(runtimeStore);
        if (state.status === 'COMPLETED' || state.status === 'FAILED') {
            clearTimeout(autoRunTimer);
            return;
        }
        autoStepLoop();
    }, 800); 
}

export function resetSimulation() {
    if (autoRunTimer) {
        clearTimeout(autoRunTimer);
        autoRunTimer = null;
    }

    if (ws) {
        ws.close();
        ws = null;
    }

    // [[FIX]] Clear the processed invocations cache
    processedInvocations.clear();

    if (originalTopologySnapshot) {
        const restoredNodes = originalTopologySnapshot.nodes.map(node => ({
            ...node,
            status: 'idle' as const
        }));

        const restoredEdges = originalTopologySnapshot.edges.map(edge => ({
            ...edge,
            active: false,
            finalized: false,
            pulseAnimation: false,
            signatureHash: undefined
        }));

        agentNodes.set(restoredNodes);
        pipelineEdges.set(restoredEdges);

        originalTopologySnapshot = null;
    } else {
        agentNodes.update(nodes => nodes.map(n => ({ ...n, status: 'idle' as const })));
        pipelineEdges.update(edges => edges.map(e => ({
            ...e,
            active: false,
            finalized: false,
            pulseAnimation: false,
            signatureHash: undefined
        })));
    }

    logs.set([]);
    runtimeStore.set({ status: 'IDLE', runId: null });
    addLog('SIMULATOR', 'Context cleared.', 'RESET');
}