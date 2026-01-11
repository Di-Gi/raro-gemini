// [[RARO]]/apps/web-console/src/lib/templates.ts
// Change Type: New
// Purpose: Define static graph topologies for the Template System
// Architectural Context: Data Abstraction Layer for UI Presets
// Dependencies: None

import type { AgentNode, PipelineEdge } from './stores';

export interface GraphTemplate {
    key: string;
    label: string;
    nodes: AgentNode[];
    edges: PipelineEdge[];
}

export const TEMPLATES: Record<string, GraphTemplate> = {
    STANDARD: {
        key: 'STANDARD',
        label: 'GENERALIST',
        nodes: [
            { id: 'orchestrator', label: 'ORCHESTRATOR', x: 20, y: 50, model: 'reasoning', prompt: 'Analyze the user request and determine optimal sub-tasks.', status: 'idle', role: 'orchestrator', acceptsDirective: true, allowDelegation: true },
            { id: 'retrieval', label: 'RETRIEVAL', x: 50, y: 30, model: 'fast', prompt: 'Search knowledge base for relevant context.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
            { id: 'code_interpreter', label: 'CODE_INTERP', x: 50, y: 70, model: 'fast', prompt: 'Execute Python analysis on provided data.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
            { id: 'synthesis', label: 'SYNTHESIS', x: 80, y: 50, model: 'thinking', prompt: 'Synthesize all findings into a final report.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false }
        ],
        edges: [
            { from: 'orchestrator', to: 'retrieval', active: false, finalized: false, pulseAnimation: false },
            { from: 'orchestrator', to: 'code_interpreter', active: false, finalized: false, pulseAnimation: false },
            { from: 'retrieval', to: 'synthesis', active: false, finalized: false, pulseAnimation: false },
            { from: 'code_interpreter', to: 'synthesis', active: false, finalized: false, pulseAnimation: false }
        ]
    },
    RESEARCH: {
        key: 'RESEARCH',
        label: 'DEEP RESEARCH',
        nodes: [
            { id: 'planner', label: 'PLANNER', x: 15, y: 50, model: 'reasoning', prompt: 'Break down the research topic into search queries.', status: 'idle', role: 'orchestrator', acceptsDirective: true, allowDelegation: true },
            { id: 'web_searcher', label: 'WEB_SEARCH', x: 45, y: 30, model: 'fast', prompt: 'Search the web for latest information.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
            { id: 'fact_checker', label: 'VALIDATOR', x: 45, y: 70, model: 'fast', prompt: 'Verify claims against reputable sources.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
            { id: 'analyst', label: 'ANALYST', x: 70, y: 50, model: 'reasoning', prompt: 'Analyze gathered data for patterns.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
            { id: 'writer', label: 'WRITER', x: 90, y: 50, model: 'thinking', prompt: 'Write a comprehensive research report.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false }
        ],
        edges: [
            { from: 'planner', to: 'web_searcher', active: false, finalized: false, pulseAnimation: false },
            { from: 'planner', to: 'fact_checker', active: false, finalized: false, pulseAnimation: false },
            { from: 'web_searcher', to: 'analyst', active: false, finalized: false, pulseAnimation: false },
            { from: 'fact_checker', to: 'analyst', active: false, finalized: false, pulseAnimation: false },
            { from: 'analyst', to: 'writer', active: false, finalized: false, pulseAnimation: false }
        ]
    },
    DEV: {
        key: 'DEV',
        label: 'DEV STUDIO',
        nodes: [
            { id: 'architect', label: 'ARCHITECT', x: 20, y: 50, model: 'reasoning', prompt: 'Design the software architecture and file structure.', status: 'idle', role: 'orchestrator', acceptsDirective: true, allowDelegation: true },
            { id: 'coder', label: 'PYTHON_DEV', x: 50, y: 30, model: 'fast', prompt: 'Implement the solution in Python.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
            { id: 'reviewer', label: 'CODE_REVIEW', x: 50, y: 70, model: 'reasoning', prompt: 'Review code for bugs and security issues.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
            { id: 'runner', label: 'TEST_RUNNER', x: 80, y: 50, model: 'fast', prompt: 'Execute the code and capture output.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false }
        ],
        edges: [
            { from: 'architect', to: 'coder', active: false, finalized: false, pulseAnimation: false },
            { from: 'architect', to: 'reviewer', active: false, finalized: false, pulseAnimation: false },
            { from: 'coder', to: 'runner', active: false, finalized: false, pulseAnimation: false },
            { from: 'reviewer', to: 'runner', active: false, finalized: false, pulseAnimation: false }
        ]
    }
};