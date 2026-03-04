#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# SplitWise — One-command local setup
# Usage:  bash install.sh
# ─────────────────────────────────────────────────────────────
set -e

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     SplitWise  —  Local Setup        ║"
echo "╚══════════════════════════════════════╝"
echo ""

# 1. Python check
if ! command -v python3 &>/dev/null; then
    echo "❌  Python 3 not found. Install from https://python.org" && exit 1
fi
PY=$(python3 --version)
info "Found $PY"

# 2. Virtual environment
if [ ! -d "venv" ]; then
    info "Creating virtual environment..."
    python3 -m venv venv
    success "venv created"
else
    info "venv already exists — skipping"
fi

# 3. Activate
source venv/bin/activate

# 4. Install dependencies
info "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
success "Dependencies installed"

# 5. Optional OCR
read -rp "$(echo -e "${YELLOW}Install OCR support (pytesseract + Pillow)? [y/N]:${NC} ")" ocr
if [[ "$ocr" =~ ^[Yy]$ ]]; then
    pip install pytesseract Pillow -q
    success "OCR packages installed"
    warn "You still need the Tesseract binary:"
    warn "  Ubuntu/Debian: sudo apt install tesseract-ocr"
    warn "  macOS:         brew install tesseract"
    warn "  Windows:       https://github.com/UB-Mannheim/tesseract/wiki"
fi

# 6. .env file
if [ ! -f ".env" ]; then
    cp .env.example .env
    # Generate a random secret key
    SK=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i.bak "s/CHANGE_ME_TO_A_RANDOM_32_CHAR_STRING/$SK/" .env && rm -f .env.bak
    success ".env created with random SECRET_KEY"
else
    info ".env already exists — skipping"
fi

# 7. Directories
mkdir -p instance uploads
success "Directories ready"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   ✅  Setup complete!                ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  Start the app:"
echo "    source venv/bin/activate"
echo "    python app.py"
echo ""
echo "  Then open:  http://localhost:5000"
echo "  Demo login: amaya@demo.com / demo123"
echo "              (click 'Load Demo Data' on the login page)"
echo ""
