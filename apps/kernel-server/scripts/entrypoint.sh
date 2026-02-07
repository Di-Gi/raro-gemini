#!/bin/bash
set -e
mkdir -p /app/storage/library/public

# 1. Financial/Telemetry Data (For Scenario: Financial Audit)
if [ ! -f "/app/storage/library/public/raw_telemetry_dump.csv" ]; then
    echo "timestamp,node_id,metric_type,value,threshold,variance,severity
2024-02-06T10:00:00Z,node-01,latency_ms,42.5,50,0.15,INFO
2024-02-06T10:05:00Z,node-02,latency_ms,185.2,50,3.2,CRITICAL
2024-02-06T10:10:00Z,node-01,latency_ms,48.1,50,0.05,INFO
2024-02-06T10:25:00Z,node-01,cpu_pct,98.2,80,1.2,CRITICAL" > /app/storage/library/public/raw_telemetry_dump.csv
fi

if [ ! -f "/app/storage/library/public/financials_Q4.csv" ]; then
    echo "period,revenue,expenses,profit_margin,anomaly_detected
Q1,1250000,980000,0.21,false
Q2,1420000,1100000,0.22,false
Q3,1100000,1050000,0.04,true" > /app/storage/library/public/financials_Q4.csv
fi

# 2. Legacy Python Script (For Scenario: Code Migration)
if [ ! -f "/app/storage/library/public/legacy_script.py" ]; then
    echo "def process_data(items):
    # This is a legacy loop that needs refactoring
    results = []
    for item in items:
        if item['val'] > 10:
            results.append(item['val'] * 1.5)
    return results

data = [{'id': 1, 'val': 5}, {'id': 2, 'val': 15}]
print(process_data(data))" > /app/storage/library/public/legacy_script.py
fi

# 3. Cortex Policy (For Scenario: Safety Audit)
if [ ! -f "/app/storage/library/public/cortex_safety_policy.json" ]; then
    echo '{
  "policy_name": "RARO_CORE_SAFETY",
  "restrictions": ["fs_delete", "sudo", "network_ssh"],
  "approval_required": ["web_search", "execute_python"]
}' > /app/storage/library/public/cortex_safety_policy.json
fi

echo "[RARO] Storage initialized with unified Scenario data."
exec "$@"