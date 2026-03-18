#!/usr/bin/env bash
# =========================================================================
#  BOT Exchange Rate Processor — Single-Command Setup (macOS/Linux)
# =========================================================================
#  Usage:  chmod +x setup.sh && ./setup.sh
#  This script performs a full environment setup from scratch:
#    1. Checks for Python 3 and Git
#    2. Creates a virtual environment
#    3. Installs all dependencies
#    4. Prompts for API credentials
#    5. Installs git hooks
#    6. Opens the project folder
# =========================================================================

set -e

# ── Colors ───────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

clear
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║                                                        ║${NC}"
echo -e "${CYAN}║${BOLD}   BOT Exchange Rate Processor — Environment Setup     ${NC}${CYAN}║${NC}"
echo -e "${CYAN}║                                                        ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ═════════════════════════════════════════════════════════════════════════
#  PRIORITY 1: Strict Dependency Checks
# ═════════════════════════════════════════════════════════════════════════
echo -e "${BOLD}[1/5] Checking system dependencies...${NC}"
echo ""

FAIL=0

# ── Python check ─────────────────────────────────────────────────────────
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 --version 2>&1)
    echo -e "  ${GREEN}✓${NC} Python found: ${PY_VERSION}"
else
    echo -e "  ${RED}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "  ${RED}║  ✗ FATAL: Python 3 is NOT installed.               ║${NC}"
    echo -e "  ${RED}║                                                    ║${NC}"
    echo -e "  ${RED}║  Download from:                                    ║${NC}"
    echo -e "  ${RED}║  https://www.python.org/downloads/                 ║${NC}"
    echo -e "  ${RED}║                                                    ║${NC}"
    echo -e "  ${RED}║  macOS (Homebrew):  brew install python             ║${NC}"
    echo -e "  ${RED}║  Ubuntu/Debian:     sudo apt install python3        ║${NC}"
    echo -e "  ${RED}╚══════════════════════════════════════════════════════╝${NC}"
    FAIL=1
fi

# ── Git check ────────────────────────────────────────────────────────────
if command -v git &>/dev/null; then
    GIT_VERSION=$(git --version 2>&1)
    echo -e "  ${GREEN}✓${NC} Git found: ${GIT_VERSION}"
else
    echo -e "  ${RED}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "  ${RED}║  ✗ FATAL: Git is NOT installed.                    ║${NC}"
    echo -e "  ${RED}║                                                    ║${NC}"
    echo -e "  ${RED}║  Download from:                                    ║${NC}"
    echo -e "  ${RED}║  https://git-scm.com/downloads                    ║${NC}"
    echo -e "  ${RED}║                                                    ║${NC}"
    echo -e "  ${RED}║  macOS (Homebrew):  brew install git                ║${NC}"
    echo -e "  ${RED}║  Ubuntu/Debian:     sudo apt install git            ║${NC}"
    echo -e "  ${RED}╚══════════════════════════════════════════════════════╝${NC}"
    FAIL=1
fi

if [ $FAIL -eq 1 ]; then
    echo ""
    echo -e "${RED}Setup cannot continue. Install the missing tools above and re-run.${NC}"
    echo ""
    exit 1
fi

echo ""

# ═════════════════════════════════════════════════════════════════════════
#  PRIORITY 2: Virtual Environment & Dependencies
# ═════════════════════════════════════════════════════════════════════════
echo -e "${BOLD}[2/5] Setting up Python virtual environment...${NC}"

if [ ! -d "$PROJECT_DIR/venv" ]; then
    python3 -m venv "$PROJECT_DIR/venv"
    echo -e "  ${GREEN}✓${NC} Virtual environment created."
else
    echo -e "  ${CYAN}→${NC} Virtual environment already exists, reusing."
fi

echo -e "  ${CYAN}→${NC} Installing dependencies (this may take a minute)..."
"$PROJECT_DIR/venv/bin/pip" install -q --upgrade pip 2>/dev/null
"$PROJECT_DIR/venv/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt" 2>/dev/null
echo -e "  ${GREEN}✓${NC} All dependencies installed."
echo ""

# ═════════════════════════════════════════════════════════════════════════
#  PRIORITY 3: API Credential Setup
# ═════════════════════════════════════════════════════════════════════════
echo -e "${BOLD}[3/5] Configuring API credentials...${NC}"

if [ -f "$PROJECT_DIR/.env" ]; then
    echo -e "  ${CYAN}→${NC} .env file already exists. Skipping credential setup."
    echo -e "  ${CYAN}  (Delete .env and re-run setup to reconfigure)${NC}"
else
    echo ""
    echo -e "  ${YELLOW}You need two API keys from the Bank of Thailand.${NC}"
    echo -e "  ${YELLOW}Register at: ${BOLD}https://apiportal.bot.or.th/${NC}"
    echo ""

    read -rp "  Paste your Exchange Rate API key (BOT_TOKEN_EXG): " TOKEN_EXG
    read -rp "  Paste your Holiday API key      (BOT_TOKEN_HOL): " TOKEN_HOL

    if [ -z "$TOKEN_EXG" ] || [ -z "$TOKEN_HOL" ]; then
        echo ""
        echo -e "  ${YELLOW}⚠ One or both keys were blank.${NC}"
        echo -e "  ${YELLOW}  A .env file was created with placeholders.${NC}"
        echo -e "  ${YELLOW}  Edit .env manually before running the app.${NC}"
        TOKEN_EXG="${TOKEN_EXG:-paste_your_exchange_rate_api_key_here}"
        TOKEN_HOL="${TOKEN_HOL:-paste_your_holiday_api_key_here}"
    fi

    cat > "$PROJECT_DIR/.env" <<EOF
# BOT Exchange Rate Processor — API Credentials
# Generated by setup.sh on $(date '+%Y-%m-%d %H:%M:%S')
BOT_TOKEN_EXG=${TOKEN_EXG}
BOT_TOKEN_HOL=${TOKEN_HOL}
EOF

    echo -e "  ${GREEN}✓${NC} .env file created successfully."
fi
echo ""

# ═════════════════════════════════════════════════════════════════════════
#  Git Hooks
# ═════════════════════════════════════════════════════════════════════════
echo -e "${BOLD}[4/5] Installing git hooks...${NC}"

if [ -d "$PROJECT_DIR/.git" ] && [ -f "$PROJECT_DIR/scripts/pre-push" ]; then
    cp "$PROJECT_DIR/scripts/pre-push" "$PROJECT_DIR/.git/hooks/pre-push"
    chmod +x "$PROJECT_DIR/.git/hooks/pre-push"
    echo -e "  ${GREEN}✓${NC} Pre-push version audit hook installed."
else
    echo -e "  ${CYAN}→${NC} Skipped (not a git repository or hook not found)."
fi
echo ""

# ═════════════════════════════════════════════════════════════════════════
#  Folder Reveal & Success
# ═════════════════════════════════════════════════════════════════════════
echo -e "${BOLD}[5/5] Finalizing...${NC}"
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                                                        ║${NC}"
echo -e "${GREEN}║   ✓  Setup Complete!                                   ║${NC}"
echo -e "${GREEN}║                                                        ║${NC}"
echo -e "${GREEN}║   You can now run the application:                     ║${NC}"
echo -e "${GREEN}║                                                        ║${NC}"
echo -e "${GREEN}║     ./venv/bin/python main.py                          ║${NC}"
echo -e "${GREEN}║                                                        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# Open the project folder in the native file manager
if [[ "$OSTYPE" == "darwin"* ]]; then
    open "$PROJECT_DIR"
elif command -v xdg-open &>/dev/null; then
    xdg-open "$PROJECT_DIR"
fi
