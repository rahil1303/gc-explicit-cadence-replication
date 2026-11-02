#!/usr/bin/env bash

set -euo pipefail

URL="http://localhost:9966/petclinic/api/owners"

for _ in $(seq 1 5000); do
    curl -s -o /dev/null -f "$URL"
done