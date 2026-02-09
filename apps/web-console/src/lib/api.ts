// [[RARO]]/apps/web-console/src/lib/api.ts
import { mockStartRun, mockGetArtifact, mockResumeRun, mockStopRun, mockGetLibraryFiles, getMockGeneratedFile } from './mock-api';
import { generateId } from './stores'

// --- 1. SESSION IDENTITY LOGIC ---
const STORAGE_KEY = 'raro_session_id';


function getClientId(): string {
    if (typeof localStorage === 'undefined') return 'cli-mode'; // SSR safety
    let id = localStorage.getItem(STORAGE_KEY);
    if (!id) {
        id = generateId();
        localStorage.setItem(STORAGE_KEY, id);
        console.log('[RARO] New Session Identity Created:', id);
    }
    return id;
}

const SESSION_ID = getClientId();

// --- 2. AUTHENTICATED FETCH WRAPPER ---
async function secureFetch(url: string, options: RequestInit = {}): Promise<Response> {
    const headers = new Headers(options.headers || {});

    // Inject the Session ID
    headers.set('X-RARO-CLIENT-ID', SESSION_ID);

    // Ensure JSON content type if not set (convenience)
    if (!headers.has('Content-Type') && !(options.body instanceof FormData)) {
        headers.set('Content-Type', 'application/json');
    }

    return fetch(url, { ...options, headers });
}

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
  // === RFS Integration ===
  attached_files?: string[];
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
  user_directive?: string;        // Runtime task from operator
  accepts_directive?: boolean;    // Can this node receive operator directives?
  allow_delegation?: boolean;
}

export async function startRun(config: WorkflowConfig): Promise<{ success: boolean; run_id: string }> {
  // ** MOCK INTERCEPTION **
  if (USE_MOCK) {
    return mockStartRun(config);
  }

  try {
    const res = await secureFetch(`${KERNEL_API}/runtime/start`, {
      method: 'POST',
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
  // ** HYBRID MODE DETECTION **
  // Intercept simulation IDs even if USE_MOCK is false globally
  if (USE_MOCK || (runId && (runId.startsWith('sim-') || runId.startsWith('mock-')))) {
    return mockGetArtifact(runId, agentId);
  }

  try {
    // Use secureFetch to pass X-RARO-CLIENT-ID header
    const res = await secureFetch(`${KERNEL_API}/runtime/${runId}/artifact/${agentId}`);

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
                    position: { x: 30, y: 50 },
                    accepts_directive: false
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
                    position: { x: 70, y: 50 },
                    accepts_directive: false
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

// === RFS API ===

export async function getLibraryFiles(): Promise<string[]> {
    if (USE_MOCK) {
        return mockGetLibraryFiles();
    }

    try {
        const res = await secureFetch(`${KERNEL_API}/runtime/library`);
        if (!res.ok) throw new Error('Failed to fetch library');
        const data = await res.json();
        return data.files || [];
    } catch (e) {
        console.error('Library fetch failed:', e);
        return [];
    }
}

export async function uploadFile(file: File): Promise<string> {
    if (USE_MOCK) {
        console.warn("[MOCK] Upload simulated.");
        return new Promise(resolve => setTimeout(() => resolve("success"), 1000));
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await secureFetch(`${KERNEL_API}/runtime/library/upload`, {
            method: 'POST',
            body: formData, // secureFetch handles the auth header, browser sets Content-Type for FormData
        });

        if (!res.ok) {
            throw new Error(`Upload failed: ${res.statusText}`);
        }

        return "success";
    } catch (e) {
        console.error('Upload API Error:', e);
        throw e;
    }
}

// === ARTIFACT STORAGE API ===

export interface ArtifactFile {
    filename: string;
    agent_id: string;
    generated_at: string;
    size_bytes: number;
    content_type: string;
}

export interface ArtifactMetadata {
    run_id: string;
    workflow_id: string;
    user_directive: string;
    created_at: string;
    expires_at: string;
    artifacts: ArtifactFile[];
    status: string;
}

export async function getAllArtifacts(): Promise<ArtifactMetadata[]> {
    if (USE_MOCK) {
        // Mock will be implemented in mock-api.ts
        const { mockGetAllArtifacts } = await import('./mock-api');
        return mockGetAllArtifacts();
    }

    try {
        const res = await secureFetch(`${KERNEL_API}/runtime/artifacts`);
        if (!res.ok) throw new Error('Failed to fetch artifacts');
        const data = await res.json();
        return data.artifacts.map((a: any) => a.metadata);
    } catch (e) {
        console.error('Artifacts fetch failed:', e);
        return [];
    }
}

export async function getRunArtifacts(runId: string): Promise<ArtifactMetadata | null> {
    if (USE_MOCK) {
        const { mockGetRunArtifacts } = await import('./mock-api');
        return mockGetRunArtifacts(runId);
    }

    try {
        const res = await fetch(`${KERNEL_API}/runtime/artifacts/${runId}`);
        if (res.status === 404) return null;
        if (!res.ok) throw new Error('Failed to fetch run artifacts');
        return await res.json();
    } catch (e) {
        console.error('Run artifacts fetch failed:', e);
        return null;
    }
}

export async function deleteArtifactRun(runId: string): Promise<void> {
    if (USE_MOCK) {
        console.warn("[MOCK] Delete artifact run simulated.");
        return;
    }

    try {
        const res = await secureFetch(`${KERNEL_API}/runtime/artifacts/${runId}`, {
            method: 'DELETE'
        });
        if (!res.ok) throw new Error('Failed to delete artifact run');
    } catch (e) {
        console.error('Artifact deletion failed:', e);
        throw e;
    }
}

export async function promoteArtifactToLibrary(runId: string, filename: string): Promise<void> {
    if (USE_MOCK) {
        console.warn("[MOCK] Promote artifact simulated.");
        return;
    }

    try {
        const res = await secureFetch(`${KERNEL_API}/runtime/artifacts/${runId}/files/${filename}/promote`, {
            method: 'POST'
        });
        if (!res.ok) throw new Error('Failed to promote artifact');
    } catch (e) {
        console.error('Artifact promotion failed:', e);
        throw e;
    }
}

export function getArtifactFileUrl(runId: string, filename: string): string {
    if (USE_MOCK) {
        // In mock mode, return data URL directly
        const mockUrl = getMockGeneratedFile(filename);
        if (mockUrl) return mockUrl;

        // Fallback to placeholder for unknown files
        return 'data:text/plain;charset=utf-8,' + encodeURIComponent(`Mock file: ${filename}\n\nNo preview available in demo mode.`);
    }

    return `${KERNEL_API}/runtime/artifacts/${runId}/files/${filename}`;
}