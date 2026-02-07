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
            { id: 'master_orchestrator', label: 'ORCHESTRATOR', x: 20, y: 50, model: 'reasoning', prompt: 'Analyze the user request and determine optimal sub-tasks.', status: 'idle', role: 'orchestrator', acceptsDirective: true, allowDelegation: true, tools: ['web_search', 'execute_python', 'write_file'] },
            { id: 'research_retrieval', label: 'RETRIEVAL', x: 50, y: 30, model: 'fast', prompt: 'Search knowledge base and web for relevant context.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false, tools: ['web_search'] },
            { id: 'analyze_interpreter', label: 'CODE_INTERP', x: 50, y: 70, model: 'fast', prompt: 'Execute Python analysis on provided data.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false, tools: ['execute_python'] },
            { id: 'writer_synthesis', label: 'SYNTHESIS', x: 80, y: 50, model: 'thinking', prompt: 'Synthesize all findings into a final report.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false, tools: ['write_file'] }
        ],
        edges: [
            { from: 'master_orchestrator', to: 'research_retrieval', active: false, finalized: false, pulseAnimation: false },
            { from: 'master_orchestrator', to: 'analyze_interpreter', active: false, finalized: false, pulseAnimation: false },
            { from: 'research_retrieval', to: 'writer_synthesis', active: false, finalized: false, pulseAnimation: false },
            { from: 'analyze_interpreter', to: 'writer_synthesis', active: false, finalized: false, pulseAnimation: false }
        ]
    },
    RESEARCH: {
        key: 'RESEARCH',
        label: 'DEEP RESEARCH',
        nodes: [
            { id: 'master_planner', label: 'PLANNER', x: 15, y: 50, model: 'reasoning', prompt: 'Break down the research topic into search queries.', status: 'idle', role: 'orchestrator', acceptsDirective: true, allowDelegation: true, tools: ['web_search', 'execute_python', 'write_file'] },
            { id: 'research_web', label: 'WEB_SEARCH', x: 45, y: 30, model: 'fast', prompt: 'Search the web for latest information.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false, tools: ['web_search'] },
            { id: 'research_validator', label: 'VALIDATOR', x: 45, y: 70, model: 'fast', prompt: 'Verify claims against reputable sources.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false, tools: ['web_search'] },
            { id: 'analyze_data', label: 'ANALYST', x: 70, y: 50, model: 'reasoning', prompt: 'Analyze gathered data for patterns and insights.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false, tools: ['execute_python'] },
            { id: 'writer_report', label: 'WRITER', x: 90, y: 50, model: 'thinking', prompt: 'Write a comprehensive research report.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false, tools: ['write_file'] }
        ],
        edges: [
            { from: 'master_planner', to: 'research_web', active: false, finalized: false, pulseAnimation: false },
            { from: 'master_planner', to: 'research_validator', active: false, finalized: false, pulseAnimation: false },
            { from: 'research_web', to: 'analyze_data', active: false, finalized: false, pulseAnimation: false },
            { from: 'research_validator', to: 'analyze_data', active: false, finalized: false, pulseAnimation: false },
            { from: 'analyze_data', to: 'writer_report', active: false, finalized: false, pulseAnimation: false }
        ]
    },
    DEV: {
        key: 'DEV',
        label: 'DEV STUDIO',
        nodes: [
            { id: 'master_architect', label: 'ARCHITECT', x: 20, y: 50, model: 'reasoning', prompt: 'Design the software architecture and file structure.', status: 'idle', role: 'orchestrator', acceptsDirective: true, allowDelegation: true, tools: ['web_search', 'execute_python', 'write_file'] },
            { id: 'coder_python', label: 'PYTHON_DEV', x: 50, y: 30, model: 'fast', prompt: 'Implement the solution in Python.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false, tools: ['execute_python', 'write_file'] },
            { id: 'analyze_review', label: 'CODE_REVIEW', x: 50, y: 70, model: 'reasoning', prompt: 'Review code for bugs and security issues.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false, tools: ['execute_python'] },
            { id: 'analyze_test', label: 'TEST_RUNNER', x: 80, y: 50, model: 'fast', prompt: 'Execute the code and capture output.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false, tools: ['execute_python'] }
        ],
        edges: [
            { from: 'master_architect', to: 'coder_python', active: false, finalized: false, pulseAnimation: false },
            { from: 'master_architect', to: 'analyze_review', active: false, finalized: false, pulseAnimation: false },
            { from: 'coder_python', to: 'analyze_test', active: false, finalized: false, pulseAnimation: false },
            { from: 'analyze_review', to: 'analyze_test', active: false, finalized: false, pulseAnimation: false }
        ]
    }
};