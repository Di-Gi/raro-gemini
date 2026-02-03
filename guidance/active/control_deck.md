This prototype replaces the current "dropdown soup" in the `Pipeline` pane with a **"Patch Bay" Matrix**.

It is designed to fit exactly into your existing `ControlDeck` dimensions. It uses CSS Grid for precise alignment and sticky headers for scrolling through larger graphs.

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <style>
        /* === RARO THEME VARIABLES (Simulated from your App.svelte) === */
        :root {
            --paper-bg: #050505;
            --paper-surface: #0a0a0a;
            --paper-surface-dim: #0f0f0f;
            --paper-line: #333;
            --paper-ink: #e0e0e0;
            --arctic-cyan: #00F0FF;
            --alert-amber: #FFB300;
            --font-code: 'Courier New', monospace; /* Fallback */
        }

        body {
            background-color: #111;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            font-family: var(--font-code);
        }

        /* === COMPONENT CHASSIS (Simulates #pane-pipeline) === */
        .deck-pane {
            width: 800px;
            height: 185px; /* Exact height of your ControlDeck */
            background: var(--paper-bg);
            border: 1px solid var(--paper-line);
            display: flex;
            overflow: hidden;
        }

        /* === LEFT COL: ACTIONS & LIST === */
        .sidebar {
            width: 200px;
            border-right: 1px solid var(--paper-line);
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            background: var(--paper-surface);
        }

        .section-header {
            font-size: 10px;
            font-weight: 700;
            color: var(--paper-line);
            text-transform: uppercase;
            letter-spacing: 1px;
            border-bottom: 1px solid var(--paper-line);
            padding-bottom: 4px;
            margin-bottom: 4px;
        }

        .node-list {
            flex: 1;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .node-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--paper-surface-dim);
            border: 1px solid var(--paper-line);
            padding: 6px 8px;
            font-size: 10px;
            color: var(--paper-ink);
            cursor: pointer;
        }
        .node-item:hover { border-color: var(--paper-ink); }
        .node-item .del-btn { opacity: 0.3; cursor: pointer; }
        .node-item .del-btn:hover { opacity: 1; color: #d32f2f; }

        .add-node-btn {
            background: transparent;
            border: 1px dashed var(--paper-line);
            color: var(--paper-line);
            padding: 8px;
            font-size: 10px;
            cursor: pointer;
            text-align: center;
            text-transform: uppercase;
            transition: all 0.2s;
        }
        .add-node-btn:hover { border-color: var(--arctic-cyan); color: var(--arctic-cyan); }

        /* === RIGHT COL: THE PATCH BAY (MATRIX) === */
        .matrix-wrapper {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            position: relative;
        }

        .matrix-scroll {
            overflow: auto;
            padding: 20px;
            /* Scrollbar styling */
            scrollbar-width: thin;
            scrollbar-color: var(--paper-line) var(--paper-bg);
        }

        /* THE GRID */
        .patch-grid {
            display: grid;
            /* 
               First col: Auto (Row Labels)
               Rest: Repeat based on node count (Target Labels)
            */
            grid-template-columns: 100px repeat(4, 32px); 
            gap: 2px;
        }

        /* CORNER (Empty) */
        .corner-cell {
            grid-column: 1;
            grid-row: 1;
            border-bottom: 1px solid var(--paper-line);
            border-right: 1px solid var(--paper-line);
            background: var(--paper-bg);
            z-index: 20;
            position: sticky;
            top: 0;
            left: 0;
        }
        
        .corner-hint {
            position: absolute;
            bottom: 4px; right: 4px;
            font-size: 7px; color: var(--paper-line);
            text-align: right;
            line-height: 1;
        }

        /* COLUMN HEADERS (Targets) */
        .col-header {
            grid-row: 1;
            height: 100px; /* Space for vertical text */
            display: flex;
            align-items: flex-end;
            justify-content: center;
            padding-bottom: 8px;
            position: sticky;
            top: 0;
            background: var(--paper-bg);
            z-index: 10;
            border-bottom: 1px solid var(--paper-line);
        }

        .v-text {
            writing-mode: vertical-rl;
            transform: rotate(180deg);
            font-size: 9px;
            color: var(--paper-line);
            font-weight: 700;
            white-space: nowrap;
            letter-spacing: 1px;
            cursor: default;
        }

        /* ROW HEADERS (Sources) */
        .row-header {
            grid-column: 1;
            display: flex;
            align-items: center;
            justify-content: flex-end;
            padding-right: 12px;
            font-size: 9px;
            color: var(--paper-line);
            font-weight: 700;
            position: sticky;
            left: 0;
            background: var(--paper-bg);
            z-index: 10;
            border-right: 1px solid var(--paper-line);
            text-transform: uppercase;
        }

        /* THE CELL */
        .cell {
            width: 32px;
            height: 32px;
            background: var(--paper-surface-dim);
            border: 1px solid var(--paper-line);
            cursor: pointer;
            position: relative;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.1s;
        }

        .cell:hover {
            border-color: var(--paper-ink);
            background: var(--paper-surface);
        }

        /* Disabled (Self-Loop) */
        .cell.disabled {
            background: 
                repeating-linear-gradient(
                    45deg,
                    var(--paper-bg),
                    var(--paper-bg) 2px,
                    var(--paper-line) 2px,
                    var(--paper-line) 3px
                );
            cursor: not-allowed;
            opacity: 0.3;
        }
        .cell.disabled:hover { border-color: var(--paper-line); }

        /* Active (Connected) */
        .cell.active {
            background: rgba(0, 240, 255, 0.1);
            border-color: var(--arctic-cyan);
            box-shadow: inset 0 0 8px rgba(0, 240, 255, 0.2);
        }

        /* The "LED" inside the cell */
        .led {
            width: 6px;
            height: 6px;
            background: var(--paper-line);
            border-radius: 50%;
            transition: all 0.2s;
        }

        .cell.active .led {
            background: var(--arctic-cyan);
            box-shadow: 0 0 6px var(--arctic-cyan);
        }

        /* Hover Guides */
        .cell:hover::after {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            border: 1px solid var(--paper-ink);
        }

    </style>
</head>
<body>

    <!-- THE PANE SIMULATION -->
    <div class="deck-pane">
        
        <!-- SIDEBAR: Node Management -->
        <div class="sidebar">
            <div class="section-header">NODES [4]</div>
            <div class="node-list">
                <div class="node-item">
                    <span>ORCHESTRATOR</span>
                    <span class="del-btn">×</span>
                </div>
                <div class="node-item">
                    <span>RETRIEVAL</span>
                    <span class="del-btn">×</span>
                </div>
                <div class="node-item">
                    <span>CODER</span>
                    <span class="del-btn">×</span>
                </div>
                <div class="node-item">
                    <span>SYNTHESIS</span>
                    <span class="del-btn">×</span>
                </div>
            </div>
            <button class="add-node-btn">+ ADD NODE</button>
        </div>

        <!-- MATRIX AREA -->
        <div class="matrix-wrapper">
            <div class="matrix-scroll">
                <div class="patch-grid">
                    
                    <!-- CORNER -->
                    <div class="corner-cell">
                        <div class="corner-hint">
                            SRC<br>▼<br>TGT ▶
                        </div>
                    </div>

                    <!-- COLUMN HEADERS (Targets) -->
                    <div class="col-header"><span class="v-text">ORCHESTRATOR</span></div>
                    <div class="col-header"><span class="v-text">RETRIEVAL</span></div>
                    <div class="col-header"><span class="v-text">CODER</span></div>
                    <div class="col-header"><span class="v-text">SYNTHESIS</span></div>

                    <!-- ROW 1: ORCHESTRATOR -->
                    <div class="row-header">ORCHESTRATOR</div>
                    <div class="cell disabled"></div> <!-- Self -->
                    <div class="cell active"><div class="led"></div></div> <!-- -> Retrieval -->
                    <div class="cell active"><div class="led"></div></div> <!-- -> Coder -->
                    <div class="cell"><div class="led"></div></div>

                    <!-- ROW 2: RETRIEVAL -->
                    <div class="row-header">RETRIEVAL</div>
                    <div class="cell"><div class="led"></div></div>
                    <div class="cell disabled"></div> <!-- Self -->
                    <div class="cell"><div class="led"></div></div>
                    <div class="cell active"><div class="led"></div></div> <!-- -> Synthesis -->

                    <!-- ROW 3: CODER -->
                    <div class="row-header">CODER</div>
                    <div class="cell"><div class="led"></div></div>
                    <div class="cell"><div class="led"></div></div>
                    <div class="cell disabled"></div> <!-- Self -->
                    <div class="cell active"><div class="led"></div></div> <!-- -> Synthesis -->

                    <!-- ROW 4: SYNTHESIS -->
                    <div class="row-header">SYNTHESIS</div>
                    <div class="cell"><div class="led"></div></div>
                    <div class="cell"><div class="led"></div></div>
                    <div class="cell"><div class="led"></div></div>
                    <div class="cell disabled"></div> <!-- Self -->

                </div>
            </div>
        </div>

    </div>

</body>
</html>
```

### Implementation Notes for `ControlDeck.svelte`

1.  **CSS Grid Construction:**
    In Svelte, you'll dynamically set the grid columns style based on `$agentNodes.length`.
    ```html
    <div 
      class="patch-grid" 
      style="grid-template-columns: 100px repeat({$agentNodes.length}, 32px);"
    >
    ```

2.  **Cell Logic:**
    Iterate through `$agentNodes` as `source` (Rows), and inside that loop, iterate `$agentNodes` again as `target` (Columns).
    ```javascript
    // Is Connected?
    const isConnected = $pipelineEdges.some(e => e.from === source.id && e.to === target.id);
    // Is Self?
    const isSelf = source.id === target.id;
    ```

3.  **Click Handler:**
    ```javascript
    function toggleEdge(from, to) {
        if (from === to) return;
        if (isConnected(from, to)) {
            removeConnection(from, to);
        } else {
            addConnection(from, to);
        }
    }
    ```

4.  **Why this works:**
    *   **Density:** It fits a lot of information in 185px height.
    *   **Clarity:** The "Flow" is read left-to-right (Source Row -> Target Column).
    *   **Aesthetic:** The "Patch Bay" look fits the wireframe/tactical theme perfectly. It looks like a routing table in a network switch.