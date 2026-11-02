#!/usr/bin/env bash

set -euo pipefail

URL="http://localhost:12342/api/v1/userservice/users/fdse_microservice"
DONE_FILE="${WORKLOAD_DONE_FILE:-}"

for _ in $(seq 1 10000); do
    curl -s -o /dev/null -f "$URL"
done

if [ -n "$DONE_FILE" ]; then
    touch "$DONE_FILE"
fi