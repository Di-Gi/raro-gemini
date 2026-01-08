The reason the "Agent ID" field is currently **non-interactive** in `ControlDeck.svelte` is structural: **The ID is the Primary Key** for the entire graph.

If you simply change the ID of a node, you break:
1.  **Topology:** All edges (connections) pointing `from` or `to` that node will instantly break.
2.  **Selection State:** The `selectedNode` store holds the ID string. If the ID changes but the selection store doesn't update simultaneously, the UI panel will close or crash.

To allow renaming, you need a **transactional update** that modifies the Node, the Edges, and the Selection state in one move.

Here is the implementation plan:

### Step 1: Add a Rename Action to `stores.ts` (already complete)

Add this function to `apps/web-console/src/lib/stores.ts`. It handles the cascading updates required to keep the graph intact.

```typescript
// apps/web-console/src/lib/stores.ts

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
```

### Step 2: Update `ControlDeck.svelte`

You need to make the input editable and hook it up to the new store action.

**Locate:** `apps/web-console/src/components/ControlDeck.svelte`

**1. Update the Script Section:**
Add a local variable to handle the input state and a handler function.

```typescript
// inside <script lang="ts">
import { renameNode } from '$lib/stores'; // Import the new function

// ... existing state variables ...

let tempId = $state(''); 

// Sync tempId when the selection changes
$effect(() => {
  if ($selectedNode) {
    tempId = $selectedNode; // Initialize input with current ID
  }
});

function handleRename() {
  if (!$selectedNode || !tempId) return;
  
  // Clean the ID (remove spaces, etc if desired)
  const cleanId = tempId.trim().replace(/\s+/g, '_').toLowerCase();
  
  const success = renameNode($selectedNode, cleanId);
  
  if (success) {
    addLog('SYSTEM', `Node renamed: ${cleanId}`, 'OK');
  } else {
    // Revert if failed (duplicate or invalid)
    tempId = $selectedNode; 
    addLog('SYSTEM', `Rename failed: ID exists or invalid`, 'WARN');
  }
}

function handleIdKey(e: KeyboardEvent) {
    if (e.key === 'Enter') {
        (e.target as HTMLInputElement).blur(); // Trigger onblur
    }
}
// ...
```

**2. Update the HTML Template:**
Find the "Agent ID" form group inside `pane-node-config`. Remove `readonly`, remove the `input-readonly` class, and bind the events.

```html
<!-- inside #pane-node-config -->

<div class="form-grid">
  <div class="form-group">
    <label>Agent ID</label>
    <!-- UPDATED INPUT -->
    <input 
      class="input-std" 
      bind:value={tempId} 
      onblur={handleRename}
      onkeydown={handleIdKey}
      placeholder="Enter unique ID..."
    />
  </div>
  
  <div class="form-group">
    <!-- Model Runtime Select... -->
```

### Critical Warning
Renaming nodes is safe **before** a run starts (in "Architect" or "Idle" mode).

However, if you rename a node while the system is **RUNNING**, you will disconnect the frontend from the backend status updates, because the Kernel server still knows that node by its original ID (`orchestrator`), but your frontend is now listening for updates on `my_new_name`.

You should technically disable the input during a run:

```html
<input 
  class="input-std" 
  bind:value={tempId} 
  onblur={handleRename}
  onkeydown={handleIdKey}
  disabled={$runtimeStore.status === 'RUNNING'} 
/>
```