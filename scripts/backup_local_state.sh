#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="$ROOT_DIR/backups"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE_PATH="$BACKUP_DIR/aiagent-local-state-$TIMESTAMP.tar.gz"
MANIFEST_PATH="$BACKUP_DIR/aiagent-local-state-$TIMESTAMP.manifest.txt"

mkdir -p "$BACKUP_DIR"

declare -a INCLUDE_PATHS=()
for path in config models cache logs .venv; do
  if [[ -e "$ROOT_DIR/$path" ]]; then
    INCLUDE_PATHS+=("$path")
  fi
done

if [[ ${#INCLUDE_PATHS[@]} -eq 0 ]]; then
  echo "没有找到可备份的本地状态目录"
  exit 1
fi

{
  echo "timestamp=$TIMESTAMP"
  echo "git_head=$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "git_branch=$(git -C "$ROOT_DIR" branch --show-current 2>/dev/null || echo unknown)"
  echo "python3_version=$(python3 --version 2>&1 || echo unavailable)"
  echo "uname=$(uname -a 2>/dev/null || echo unavailable)"
  echo "ros_distros=$(ls /opt/ros 2>/dev/null | tr '\n' ' ' || echo unavailable)"
  echo "included_paths=${INCLUDE_PATHS[*]}"
} > "$MANIFEST_PATH"

tar -C "$ROOT_DIR" -czf "$ARCHIVE_PATH" \
  "${INCLUDE_PATHS[@]}" \
  requirements.txt \
  requirements_local_agent.txt \
  pyproject.toml \
  README.md

echo "本地状态已备份:"
echo "  archive : $ARCHIVE_PATH"
echo "  manifest: $MANIFEST_PATH"
