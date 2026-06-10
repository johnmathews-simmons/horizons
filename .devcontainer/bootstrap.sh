#!/usr/bin/env bash
set -e

REPO_DIR="/workspaces/setup"
REPO_URL="https://github.com/johnmathews-simmons/setup"
BRANCH="main"

if [ -d "$REPO_DIR/.git" ]; then
  echo "Setup repo exists — checking for updates..."
  cd "$REPO_DIR"

  git fetch

  if git diff --quiet HEAD origin/$BRANCH; then
    echo "No changes in setup repo — skipping install.sh"
  else
    echo "Changes detected — pulling and running install.sh"
    git pull
    bash install.sh
  fi
else
  echo "Setup repo not found — cloning and running install.sh"
  git clone "$REPO_URL" "$REPO_DIR"
  cd "$REPO_DIR"
  bash install.sh
fi
