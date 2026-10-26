#!/usr/bin/env sh
# Build the agent image and run the memory check at Render's 512 MB limit, offline.
set -eu
cd "$(dirname "$0")/.."
docker build -q -t any-plans-agent:memcheck . >/dev/null
docker run --rm -i --memory=512m --memory-swap=512m --network=none \
  -e SUPABASE_URL=https://example.supabase.co -e CONFIG_VERSION=memcheck \
  --entrypoint python any-plans-agent:memcheck - < scripts/memcheck.py 2>&1 | grep '^{'
docker image ls any-plans-agent:memcheck --format 'image size (docker image ls): {{.Size}}'
docker run --rm --entrypoint sh any-plans-agent:memcheck -c 'du -sxh / 2>/dev/null' \
  | awk '{print "image filesystem (unpacked):", $1}'
