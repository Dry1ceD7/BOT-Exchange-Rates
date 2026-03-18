#!/usr/bin/env bash
# Install git hooks for BOT Exchange Rate Processor
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cp "$SCRIPT_DIR/pre-push" "$PROJECT_DIR/.git/hooks/pre-push"
chmod +x "$PROJECT_DIR/.git/hooks/pre-push"

echo "✓ Pre-push hook installed successfully."
echo "  It will auto-audit version strings before every push."
