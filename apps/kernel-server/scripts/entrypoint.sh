#!/bin/bash
set -e

# 1. Create Public Directory
mkdir -p /app/storage/library/public

# 2. Seed Files (If they don't exist)
# In a real build, you might copy these from the source code into the image
if [ ! -f "/app/storage/library/public/legacy_script.py" ]; then
    echo "print('Seeding Legacy Script...')" > /app/storage/library/public/legacy_script.py
fi

if [ ! -f "/app/storage/library/public/financials.csv" ]; then
    echo "id,amount,variance
1,500,0.2
2,750,0.15
3,1000,0.25" > /app/storage/library/public/financials.csv
fi

# 3. Log initialization
echo "[RARO] Storage initialized with public seed data"

# 4. Start Application
exec "$@"
