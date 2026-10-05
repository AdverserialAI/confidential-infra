#!/bin/sh
# Runs inside the CVM before Docker Compose is started by Phala.
# This script is intentionally read-only: it does not prune images or volumes,
# stop containers, change model files, or alter Docker credentials.
set -eu

required_volume="${MODEL_WEIGHTS_VOLUME:-cyberglm-data}"
if ! docker volume inspect "$required_volume" >/dev/null 2>&1; then
  echo "required model volume is unavailable: $required_volume" >&2
  exit 1
fi

mountpoint=$(docker volume inspect "$required_volume" --format '{{ .Mountpoint }}')
model_path="${SGLANG_MODEL_PATH:-/data/cyberglm-fp8}"
# The compose mount maps the volume to /data. Translate only that known prefix
# to validate the host-visible path without touching model files.
case "$model_path" in
  /data/*) host_model_path="$mountpoint/${model_path#/data/}" ;;
  *) echo "SGLANG_MODEL_PATH must remain under /data" >&2; exit 1 ;;
esac
if [ ! -d "$host_model_path" ]; then
  echo "model path is unavailable in $required_volume: $model_path" >&2
  exit 1
fi

echo "confidential CVM preflight passed: existing model volume and path verified"
