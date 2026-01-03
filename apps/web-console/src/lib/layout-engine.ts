// [[RARO]]/apps/web-console/src/lib/layout-engine.ts
import type { AgentNode, PipelineEdge } from './stores';

export class DagLayoutEngine {
    static computeLayout(nodes: AgentNode[], edges: PipelineEdge[]): AgentNode[] {
        if (nodes.length === 0) return [];

        // 1. Build Graph Structure
        const adj = new Map<string, string[]>();
        const inDegree = new Map<string, number>();
        
        nodes.forEach(n => {
            adj.set(n.id, []);
            inDegree.set(n.id, 0);
        });

        edges.forEach(e => {
            if (adj.has(e.from) && inDegree.has(e.to)) {
                adj.get(e.from)?.push(e.to);
                inDegree.set(e.to, (inDegree.get(e.to) || 0) + 1);
            }
        });

        // 2. Assign Ranks (X-Axis Layering via Longest Path)
        const ranks = new Map<string, number>();
        const queue: string[] = [];

        // Find roots
        nodes.forEach(n => {
            if ((inDegree.get(n.id) || 0) === 0) {
                ranks.set(n.id, 0);
                queue.push(n.id);
            }
        });

        // Fallback for cycles/no-roots: force first node as root
        if (queue.length === 0 && nodes.length > 0) {
            ranks.set(nodes[0].id, 0);
            queue.push(nodes[0].id);
        }

        // BFS for Rank Assignment
        while (queue.length > 0) {
            const u = queue.shift()!;
            const currentRank = ranks.get(u)!;
            
            const neighbors = adj.get(u) || [];
            neighbors.forEach(v => {
                const existingRank = ranks.get(v) || 0;
                // Push child to at least parent + 1
                const newRank = Math.max(existingRank, currentRank + 1);
                ranks.set(v, newRank);
                
                // Add to queue if not processed in this specific path context
                // (Simple DAG traversal)
                if (!queue.includes(v)) queue.push(v);
            });
        }

        // 3. Assign Y-Axis (Distribute within Rank)
        const layers = new Map<number, string[]>();
        let maxRank = 0;

        ranks.forEach((rank, nodeId) => {
            if (!layers.has(rank)) layers.set(rank, []);
            layers.get(rank)?.push(nodeId);
            if (rank > maxRank) maxRank = rank;
        });

        // 4. Normalize to 0-100% Viewport
        const MARGIN_X = 10; 
        const MARGIN_Y = 15;
        const AVAILABLE_W = 100 - (MARGIN_X * 2);
        const AVAILABLE_H = 100 - (MARGIN_Y * 2);

        return nodes.map(node => {
            const rank = ranks.get(node.id) || 0;
            const layerNodes = layers.get(rank)!;
            
            // X Position
            const xPercent = maxRank === 0 
                ? 50 
                : MARGIN_X + (rank / maxRank) * AVAILABLE_W;

            // Y Position (Sort by ID for stability, or index)
            layerNodes.sort(); 
            const indexInLayer = layerNodes.indexOf(node.id);
            const countInLayer = layerNodes.length;
            
            // Distribute evenly vertically
            const yPercent = MARGIN_Y + ((indexInLayer + 1) / (countInLayer + 1)) * AVAILABLE_H;

            return { ...node, x: xPercent, y: yPercent };
        });
    }
}