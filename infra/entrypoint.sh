#!/bin/sh
set -e

IMPORT_ARGS=""
if [ -f "/data/export/firebase-export-metadata.json" ]; then
  echo "Found existing emulator export at /data/export. Importing on start..."
  IMPORT_ARGS="--import=/data/export"
else
  echo "No existing export found at /data/export. Starting clean..."
fi

# exec ensures firebase is PID 1 to receive SIGTERM
exec firebase emulators:start --project demo-study-companion $IMPORT_ARGS --export-on-exit=/data/export
