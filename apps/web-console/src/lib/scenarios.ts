// [[RARO]]/apps/web-console/src/lib/scenarios.ts
// Change Type: New
// Purpose: Define "Mission Control" scenarios for one-click demos
// Architectural Context: Accessibility Layer - Bridges templates with UX
// Dependencies: templates

import { TEMPLATES } from './templates';

export interface MissionScenario {
    id: string;
    title: string;
    description: string;
    icon: string;
    templateKey: keyof typeof TEMPLATES;
    directive: string;
    difficulty: 'EASY' | 'MEDIUM' | 'HARD';
    suggestedFiles: string[];
}

export const SCENARIOS: MissionScenario[] = [
    {
        id: 'financial_audit',
        title: 'Deep Financial Audit',
        description: 'Analyze raw CSV data, detect anomalies using Python, and generate a PDF executive summary.',
        icon: '📊',
        templateKey: 'STANDARD',
        directive: "Analyze the 'raw_telemetry_dump.csv'. Identify the top 3 anomalies based on variance. Generate a matplotlib chart of the findings, and write a 'financial_report.md' summarizing the risk factors.",
        difficulty: 'MEDIUM',
        suggestedFiles: ['raw_telemetry_dump.csv']
    },
    {
        id: 'market_research',
        title: 'Competitor Recon',
        description: 'Spawn autonomous agents to research a topic, verify facts, and synthesize a strategy document.',
        icon: '🌐',
        templateKey: 'RESEARCH',
        directive: "Research the current state of 'Solid State Battery' technology. Compare top 3 competitors. Verify production claims. Compile a 'market_strategy.md' with a SWOT analysis.",
        difficulty: 'HARD',
        suggestedFiles: []
    },
    {
        id: 'code_migration',
        title: 'Legacy Code Refactor',
        description: 'Ingest a legacy Python script, map the dependency graph, and rewrite it using modern patterns.',
        icon: '💻',
        templateKey: 'DEV',
        directive: "Read 'legacy_script.py'. Map the control flow. Refactor the 'process_data' function to use Pandas instead of raw loops. Output the new code to 'modern_script.py' and run a test validation.",
        difficulty: 'EASY',
        suggestedFiles: ['legacy_script.py']
    }
];
