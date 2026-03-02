#!/bin/bash
set -e

DATA_DIR="${DATA_DIR:-/app/data}"
VECTORDB_DIR="$DATA_DIR/vectordb"

echo "=== SEC RAG Startup ==="
echo "DATA_DIR: $DATA_DIR"
echo "OPENAI_API_KEY set: $([ -n "$OPENAI_API_KEY" ] && echo 'yes' || echo 'NO')"

# Run the full pipeline if the vector DB doesn't exist yet
if [ ! -d "$VECTORDB_DIR" ] || [ -z "$(ls -A "$VECTORDB_DIR" 2>/dev/null)" ]; then
    echo "Vector DB not found — running data pipeline (first deploy)..."
    python main.py
    echo "Pipeline complete."
else
    echo "Vector DB found — skipping pipeline."
fi

echo "Starting API server..."
exec uvicorn api:app --host 0.0.0.0 --port "${PORT:-7999}"
