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
        description: 'Analyze telemetry and financials to detect margin anomalies.',
        icon: '📊',
        templateKey: 'STANDARD',
        directive: "Read 'raw_telemetry_dump.csv' and 'financials_Q4.csv'. Correlate the 'CRITICAL' latency spikes in telemetry with the profit margin drop in Q3. Generate a matplotlib chart showing the impact and save as 'anomaly_report.png'.",
        difficulty: 'MEDIUM',
        suggestedFiles: ['raw_telemetry_dump.csv', 'financials_Q4.csv']
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
        description: 'Ingest a legacy script and refactor for performance.',
        icon: '💻',
        templateKey: 'DEV',
        directive: "Read 'legacy_script.py'. Refactor the process_data function to be more efficient using a list comprehension. Test the new logic and save as 'refactored_script.py'.",
        difficulty: 'EASY',
        suggestedFiles: ['legacy_script.py']
    }
];
