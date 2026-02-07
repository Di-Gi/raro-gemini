This specification outlines the **Frontend Integration of Identity-Based Tooling**.

It updates the `web-console` to visualize the "Identity Contract" directly on the graph nodes and provides controls for the Operator to manually provision additional capabilities in the Control Deck.

---

### 1. Store Update: Identity Logic & Interfaces

**File:** `apps/web-console/src/lib/stores.ts`

We need to update the `AgentNode` interface to track tools and add a utility to derive defaults from the Agent ID, mirroring the Kernel's logic.

```typescript
// --- [PATCH: AgentNode Interface] ---
export interface AgentNode {
  id: string;
  label: string;
  x: number;
  y: number;
  model: string;
  prompt: string;
  status: 'idle' | 'running' | 'complete' | 'failed';
  role: 'orchestrator' | 'worker' | 'observer';
  acceptsDirective: boolean;
  allowDelegation: boolean;
  tools: string[]; // <--- NEW: Store active tools
}

// --- [PATCH: Identity Utility Helper] ---
// Mirrors the Kernel's authoritative provisioning logic
export function deriveToolsFromId(id: string): string[] {
    const tools = new Set<string>();
    const lowerId = id.toLowerCase();

    // 1. Base Identity Grants
    if (lowerId.startsWith('research_')) tools.add('web_search');
    if (lowerId.startsWith('analyze_') || lowerId.startsWith('coder_')) tools.add('execute_python');
    if (lowerId.startsWith('coder_') || lowerId.startsWith('writer_')) tools.add('write_file');
    
    // 2. Master Grant
    if (lowerId.startsWith('master_')) {
        tools.add('web_search');
        tools.add('execute_python');
        tools.add('write_file');
    }

    return Array.from(tools);
}

// Update createNode to use this:
export function createNode(x: number, y: number) {
    agentNodes.update(nodes => {
        const id = `node_${Date.now().toString().slice(-4)}`;
        return [...nodes, {
            id,
            label: 'NEW_AGENT',
            x, y,
            model: 'fast',
            prompt: 'Describe task...',
            status: 'idle',
            role: 'worker',
            acceptsDirective: false,
            allowDelegation: false,
            tools: [] // Start empty, user or ID will populate
        }];
    });
}
```

---

### 2. Control Deck: Tool Configuration Panel

**File:** `apps/web-console/src/components/ControlDeck.svelte`

We add a new form group to `pane-node-config`. When the Agent ID changes (rename), we automatically toggle the tools associated with that prefix, providing immediate visual feedback of the "Identity Contract."

```svelte
<!-- Inside <script> -->
// ... existing imports
import { deriveToolsFromId } from '$lib/stores';

// ... state variables
let currentTools = $state<string[]>([]);

// ... inside the $effect for selection change
if ($selectedNode && expanded) {
    const node = $agentNodes.find((n) => n.id === $selectedNode)
    if (node) {
        // ... existing props
        currentTools = node.tools || [];
    }
}

// ... update saveNodeConfig
function saveNodeConfig() {
    if (!$selectedNode) return;
    agentNodes.update(nodes => nodes.map(n => {
        if (n.id === $selectedNode) {
            return { 
                ...n, 
                model: currentModel, 
                prompt: currentPrompt, 
                acceptsDirective: currentAcceptsDirective, 
                allowDelegation: currentAllowDelegation,
                tools: currentTools // <--- Save tools
            }
        }
        return n;
    }));
}

// ... update handleRename to trigger auto-provisioning
function handleRename() {
    if (!$selectedNode || !tempId) return;
    const cleanId = tempId.trim().replace(/\s+/g, '_').toLowerCase();
    const success = renameNode($selectedNode, cleanId);

    if (success) {
        addLog('SYSTEM', `Node renamed: ${cleanId}`, 'OK');
        
        // AUTO-PROVISIONING: Merge identity tools with existing
        const identityTools = deriveToolsFromId(cleanId);
        const merged = new Set([...currentTools, ...identityTools]);
        currentTools = Array.from(merged);
        saveNodeConfig();
        
    } else { /* ... error handling */ }
}

function toggleTool(tool: string) {
    if (currentTools.includes(tool)) {
        currentTools = currentTools.filter(t => t !== tool);
    } else {
        currentTools = [...currentTools, tool];
    }
    saveNodeConfig();
}
</script>

<!-- Inside HTML: #pane-node-config -->

<div class="form-group tool-config">
    <label>Capability Provisioning</label>
    <div class="tool-grid">
        <!-- PYTHON SANDBOX -->
        <button 
            class="tool-btn {currentTools.includes('execute_python') ? 'active' : ''}" 
            onclick={() => toggleTool('execute_python')}
        >
            <span class="tool-icon">🐍</span>
            <span class="tool-label">PYTHON_SANDBOX</span>
        </button>

        <!-- WEB SEARCH -->
        <button 
            class="tool-btn {currentTools.includes('web_search') ? 'active' : ''}" 
            onclick={() => toggleTool('web_search')}
        >
            <span class="tool-icon">🌐</span>
            <span class="tool-label">WEB_UPLINK</span>
        </button>

        <!-- FILE WRITE -->
        <button 
            class="tool-btn {currentTools.includes('write_file') ? 'active' : ''}" 
            onclick={() => toggleTool('write_file')}
        >
            <span class="tool-icon">💾</span>
            <span class="tool-label">FS_WRITE</span>
        </button>
    </div>
    <div class="tool-hint">
        Read/List file access is granted to all nodes by default.
    </div>
</div>

<style>
/* Add to existing styles */
.tool-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 8px; }
.tool-btn {
    background: var(--paper-bg); border: 1px solid var(--paper-line);
    padding: 10px 4px; display: flex; flex-direction: column; align-items: center; gap: 6px;
    cursor: pointer; border-radius: 2px; transition: all 0.2s; opacity: 0.7;
}
.tool-btn:hover { opacity: 1; border-color: var(--paper-ink); }
.tool-btn.active {
    background: var(--paper-surface-dim);
    border-color: var(--arctic-cyan);
    color: var(--paper-ink);
    opacity: 1;
    box-shadow: inset 0 0 8px rgba(0, 240, 255, 0.1);
}
.tool-icon { font-size: 16px; }
.tool-label { font-family: var(--font-code); font-size: 8px; font-weight: 700; }
.tool-hint { font-size: 9px; color: var(--paper-line); margin-top: 6px; font-style: italic; }
</style>
```

---

### 3. Pipeline Stage: Visualization Pills

**File:** `apps/web-console/src/components/PipelineStage.svelte`

We update the `renderGraph` loop to inject tool pills into the node DOM.

```typescript
// Inside renderGraph(), within the node iteration loop:

// 1. Calculate Tool Badges
// Combine explicitly stored tools with implicit ones from ID (for display robustness)
const implicitTools = deriveToolsFromId(node.id);
const allTools = Array.from(new Set([...(node.tools || []), ...implicitTools]));

// Helper to map tool names to short codes
const getToolCode = (t: string) => {
    if (t === 'execute_python') return 'PY';
    if (t === 'web_search') return 'WEB';
    if (t === 'write_file') return 'IO';
    return null;
};

// 2. Generate HTML
const toolBadgesHTML = allTools
    .map(t => {
        const code = getToolCode(t);
        return code ? `<span class="tool-pill ${code.toLowerCase()}">${code}</span>` : '';
    })
    .join('');

// 3. Inject into innerHTML template
// Place this inside <div class="unit-main">
/*
    <div class="unit-main">
        <div class="unit-role">${node.role.toUpperCase()}</div>
        <div class="unit-label">${node.label}</div>
        <div class="unit-badges">${toolBadgesHTML}</div>  <-- NEW
    </div>
*/
```

**CSS Updates (in `<style>`):**

```css
:global(.unit-badges) {
    display: flex; gap: 4px; margin-top: 6px; flex-wrap: wrap;
}

:global(.tool-pill) {
    font-family: var(--font-code); font-size: 7px; font-weight: 700;
    padding: 1px 4px; border-radius: 2px;
    border: 1px solid;
}

/* Variant Styles */
:global(.tool-pill.py) {
    border-color: #FFB300; color: #FFB300; background: rgba(255, 179, 0, 0.1);
}
:global(.tool-pill.web) {
    border-color: #00F0FF; color: #00F0FF; background: rgba(0, 240, 255, 0.1);
}
:global(.tool-pill.io) {
    border-color: #E0E0E0; color: #E0E0E0; background: rgba(255, 255, 255, 0.1);
}

/* Adjust Expanded Node Height if needed */
#pipeline-stage.expanded :global(.tactical-unit) {
    /* Ensure flex layout handles variable height gracefully */
    height: auto;
    min-height: 60px; 
}
```

---

### 4. Integration Logic: Agent Config to API

**File:** `apps/web-console/src/components/ControlDeck.svelte`

Finally, ensure `submitRun` maps these tools correctly to the backend config so the Kernel receives them.

```typescript
// Inside submitRun()

const agents: AgentConfig[] = nodes.map(n => {
    // ... existing mapping
    return {
        // ...
        // Combine manual tools with authoritative identity tools
        // The Kernel allows the user to ADD tools, but the identity ensures the BASELINE.
        tools: n.tools || [], 
        // ...
    };
});
```

### Resulting Experience

1.  **Operator Action:** You click a node and rename it to `research_competitors`.
2.  **System Response:** The "Capabilities" panel in Control Deck immediately lights up the **[WEB_UPLINK]** button.
3.  **Visual Confirmation:** The node in the Pipeline Stage displays a neon cyan **[WEB]** pill.
4.  **Manual Override:** You realize this agent also need to do math. You click **[PYTHON_SANDBOX]**.
5.  **Visual Update:** The node now shows **[WEB] [PY]**.
6.  **Runtime:** The Kernel spawns the agent with both capabilities enabled.