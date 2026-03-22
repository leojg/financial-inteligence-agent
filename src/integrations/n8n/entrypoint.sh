#!/bin/sh
# src/integrations/n8n/entrypoint.sh

# Import all workflow templates (idempotent — overwrites by ID)
for f in /data/workflows/*.json; do
  [ -f "$f" ] && n8n import:workflow --input="$f" 2>/dev/null || true
done

# Start n8n normally
exec n8n