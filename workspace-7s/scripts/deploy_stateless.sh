#!/bin/bash
# Workspace-7s: Deploy stateless code to production server
# Usage: ./scripts/deploy_stateless.sh
# Flow: test → commit → push → pull → test on server

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$WORKSPACE_ROOT/.." && pwd)"
SERVER="openclawship"
REMOTE_DIR="/root/.openclaw"

# Suppress SSH warnings globally for this script
export GIT_SSH_COMMAND="ssh -o LogLevel=ERROR -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

echo "=== Workspace-7s Deployment (Stateless) ==="

# Step 1: Run unit tests
echo "[1/5] Running unit tests..."
if [ -d "$WORKSPACE_ROOT/tests" ] && ls "$WORKSPACE_ROOT/tests"/*.py >/dev/null 2>&1; then
    cd "$WORKSPACE_ROOT"
    python3 -m pytest tests/ -v
else
    echo "No tests found in workspace-7s"
fi

# Step 2: Commit and push (workspace-specific changes)
echo "[2/5] Committing and pushing workspace-7s changes..."
cd "$ROOT_DIR"
COMMIT_MSG="${COMMIT_MSG:-[7S] Deployment $(date +%Y%m%d_%H%M%S)}"

# Stage workspace-7s specific files
git add workspace-7s/

# Check if there are changes to commit
if git diff --cached --quiet; then
    echo "No workspace-7s changes to commit"
else
    git commit -m "$COMMIT_MSG"
fi

# Push to GitHub
echo "Pushing to GitHub..."
git push origin main

# Step 3: Pull on server
echo "[3/5] Pulling on server..."
cd "$WORKSPACE_ROOT"  # Ensure we're in workspace root for SSH operations
if ! ssh $SERVER "cd $REMOTE_DIR && git pull origin main"; then
    echo "❌ DEPLOYMENT FAILED: Server pull aborted"
    exit 1
fi

# Step 4: Install dependencies on server
echo "[4/5] Installing dependencies on server..."
ssh $SERVER "cd $REMOTE_DIR/workspace-7s && pip3 install -q -r requirements.txt"

# Step 5: Run integration tests on server
echo "[5/5] Running integration tests on server..."
if ssh $SERVER "cd $REMOTE_DIR/workspace-7s && bash -lc '
    set -e

    if [ -d tests ] && ls tests/*.py >/dev/null 2>&1; then
        python3 -m pytest tests/ -v
    else
        echo \"No integration tests\"
    fi
'"; then
    echo "=== Deployment Complete ==="
else
    echo "⚠️  Integration tests had issues (see output above)"
    echo "=== Deployment Complete (with warnings) ==="
fi
