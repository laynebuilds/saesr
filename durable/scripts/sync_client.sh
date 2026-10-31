#!/bin/sh
# Regenerate the typed client from durable/src/actors.py and vendor it into the backend.
# Run after changing any actor method or model. No server needed.
set -e
cd "$(dirname "$0")/.."
uv run durable-actors generate src/actors.py --out-dir generated
rm -rf ../backend/app/actors_generated
cp -R generated ../backend/app/actors_generated
echo "vendored into backend/app/actors_generated"
