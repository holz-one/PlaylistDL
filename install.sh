#!/usr/bin/env bash
# ==============================================================================
# PlaylistDL Dependency & Environment Installer
#
# Automatically installs and configures:
#   1. System packages: ffmpeg, curl, git, rofi, kitty
#   2. uv (Fast Python package and environment manager)
#   3. Deno (JavaScript runtime for yt-dlp YouTube challenge solver)
#   4. Ollama & required models:
#      - qwen2.5:7b (Conversational AI Study Assistant)
#      - nomic-embed-text (768-dim Vector Embeddings for Semantic Search)
#   5. Pre-warms Python virtual environments & Whisper models
# ==============================================================================

set -euo pipefail

# Text formatting
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CYAN}${BOLD}"
echo "=========================================================="
echo "          PlaylistDL | Study Hall Setup & Installer        "
echo "=========================================================="
echo -e "${NC}"

# Ensure PATH includes standard local binary directories
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.deno/bin:$PATH"

# ------------------------------------------------------------------------------
# 1. Determine Sudo / Package Manager
# ------------------------------------------------------------------------------
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    fi
fi

install_system_packages() {
    log_info "Checking system utilities (ffmpeg, curl, git, unzip, rofi, kitty)..."

    local PKGS_TO_INSTALL=()

    if ! command -v ffmpeg >/dev/null 2>&1; then
        PKGS_TO_INSTALL+=("ffmpeg")
    fi
    if ! command -v curl >/dev/null 2>&1; then
        PKGS_TO_INSTALL+=("curl")
    fi
    if ! command -v git >/dev/null 2>&1; then
        PKGS_TO_INSTALL+=("git")
    fi
    if ! command -v unzip >/dev/null 2>&1; then
        PKGS_TO_INSTALL+=("unzip")
    fi
    if ! command -v rofi >/dev/null 2>&1; then
        PKGS_TO_INSTALL+=("rofi")
    fi
    if ! command -v kitty >/dev/null 2>&1; then
        PKGS_TO_INSTALL+=("kitty")
    fi

    if [ ${#PKGS_TO_INSTALL[@]} -eq 0 ]; then
        log_success "All system packages are already installed."
        return 0
    fi

    log_info "Installing missing system packages: ${PKGS_TO_INSTALL[*]}"

    if command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get update -y
        $SUDO apt-get install -y "${PKGS_TO_INSTALL[@]}"
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y "${PKGS_TO_INSTALL[@]}"
    elif command -v pacman >/dev/null 2>&1; then
        $SUDO pacman -Sy --noconfirm "${PKGS_TO_INSTALL[@]}"
    elif command -v brew >/dev/null 2>&1; then
        brew install "${PKGS_TO_INSTALL[@]}"
    else
        log_warn "Unknown package manager. Please install missing packages manually: ${PKGS_TO_INSTALL[*]}"
    fi
}

# ------------------------------------------------------------------------------
# 2. Install uv (Fast Python Package Manager)
# ------------------------------------------------------------------------------
install_uv() {
    if command -v uv >/dev/null 2>&1 || [ -x "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        log_success "uv is already installed: $(uv --version)"
        return 0
    fi

    log_info "Installing uv via official Astral installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    if command -v uv >/dev/null 2>&1; then
        log_success "uv installed successfully: $(uv --version)"
    else
        log_error "uv installation finished, but binary not found in PATH."
        exit 1
    fi

    # Add to shell profile if not already present
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [ -f "$rc" ] && ! grep -q '\.local/bin' "$rc"; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
            log_info "Added ~/.local/bin to $rc"
        fi
    done
}

# ------------------------------------------------------------------------------
# 3. Install Deno (JavaScript Runtime for yt-dlp)
# ------------------------------------------------------------------------------
install_deno() {
    if command -v deno >/dev/null 2>&1 || [ -x "$HOME/.deno/bin/deno" ]; then
        export PATH="$HOME/.deno/bin:$PATH"
        log_success "Deno is already installed: $(deno --version | head -n1)"
        return 0
    fi

    log_info "Installing Deno (used by yt-dlp for YouTube web extraction)..."
    curl -fsSL https://deno.land/install.sh | sh -s -- -y
    export PATH="$HOME/.deno/bin:$PATH"

    if command -v deno >/dev/null 2>&1; then
        log_success "Deno installed successfully: $(deno --version | head -n1)"
    else
        log_warn "Deno installation completed, but not found in current PATH."
    fi

    # Add to shell profile if not already present
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [ -f "$rc" ] && ! grep -q '\.deno/bin' "$rc"; then
            echo 'export PATH="$HOME/.deno/bin:$PATH"' >> "$rc"
            log_info "Added ~/.deno/bin to $rc"
        fi
    done
}

# ------------------------------------------------------------------------------
# 4. Install Ollama & Ensure Service is Running
# ------------------------------------------------------------------------------
install_ollama() {
    if command -v ollama >/dev/null 2>&1; then
        log_success "Ollama is already installed: $(ollama --version 2>/dev/null || echo 'installed')"
    else
        log_info "Installing Ollama..."
        if [ "$(uname)" = "Darwin" ]; then
            if command -v brew >/dev/null 2>&1; then
                brew install ollama
            else
                log_warn "Please install Ollama manually from https://ollama.com/download"
            fi
        else
            curl -fsSL https://ollama.com/install.sh | sh
        fi
    fi

    # Ensure Ollama daemon is running and responding
    log_info "Checking Ollama server status..."
    if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        log_info "Starting Ollama server..."
        if command -v systemctl >/dev/null 2>&1 && [ -n "$SUDO" ]; then
            $SUDO systemctl start ollama >/dev/null 2>&1 || true
        fi

        # If still not responding, launch daemon in background
        if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
            nohup ollama serve >/dev/null 2>&1 &
        fi

        # Wait up to 15 seconds for Ollama API to become ready
        local retries=15
        while [ $retries -gt 0 ]; do
            if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
                break
            fi
            sleep 1
            retries=$((retries - 1))
        done

        if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
            log_success "Ollama server is active and responding on http://localhost:11434"
        else
            log_warn "Ollama server did not respond yet. You may need to run 'ollama serve' in a separate terminal."
        fi
    else
        log_success "Ollama server is already running."
    fi
}

# ------------------------------------------------------------------------------
# 5. Pull Ollama AI & Embedding Models
# ------------------------------------------------------------------------------
pull_ollama_models() {
    # Read models from .env or default to standard setup
    local CHAT_MODEL="qwen2.5:7b"
    local EMBED_MODEL="nomic-embed-text"

    if [ -f ".env" ]; then
        local ENV_CHAT
        ENV_CHAT=$(grep -E '^[[:space:]]*OLLAMA_MODEL[[:space:]]*=' .env | cut -d'=' -f2 | tr -d '"'\'' ' || true)
        if [ -n "$ENV_CHAT" ]; then
            CHAT_MODEL="$ENV_CHAT"
        fi

        local ENV_EMBED
        ENV_EMBED=$(grep -E '^[[:space:]]*OLLAMA_EMBED_MODEL[[:space:]]*=' .env | cut -d'=' -f2 | tr -d '"'\'' ' || true)
        if [ -n "$ENV_EMBED" ]; then
            EMBED_MODEL="$ENV_EMBED"
        fi
    fi

    log_info "Checking Ollama models..."

    if ! command -v ollama >/dev/null 2>&1 || ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        log_warn "Ollama is not running. Skipping automated model download. Run 'ollama pull $EMBED_MODEL' and 'ollama pull $CHAT_MODEL' manually."
        return 0
    fi

    local INSTALLED_MODELS
    INSTALLED_MODELS=$(ollama list 2>/dev/null || echo "")

    # Pull Embeddings Model
    if echo "$INSTALLED_MODELS" | grep -q "$EMBED_MODEL"; then
        log_success "Embedding model '$EMBED_MODEL' is already available."
    else
        log_info "Pulling embedding model '$EMBED_MODEL' for semantic search..."
        ollama pull "$EMBED_MODEL"
        log_success "Embedding model '$EMBED_MODEL' downloaded."
    fi

    # Pull Chat Model
    if echo "$INSTALLED_MODELS" | grep -q "$CHAT_MODEL"; then
        log_success "Chat model '$CHAT_MODEL' is already available."
    else
        log_info "Pulling chat assistant model '$CHAT_MODEL'..."
        ollama pull "$CHAT_MODEL"
        log_success "Chat assistant model '$CHAT_MODEL' downloaded."
    fi
}

# ------------------------------------------------------------------------------
# 6. Pre-cache Python Virtual Environments & Whisper Model
# ------------------------------------------------------------------------------
prewarm_python_and_whisper() {
    log_info "Pre-caching Python environments with uv..."

    # Pre-cache transcribe.py and dependencies (faster-whisper, ctranslate2, duckdb)
    if [ -f "transcribe.py" ]; then
        uv run transcribe.py --help >/dev/null 2>&1 || true
    fi

    # Pre-cache downloader & CLI tools
    if [ -f "playlistDL.py" ]; then
        uv run playlistDL.py --help >/dev/null 2>&1 || true
    fi

    # Pre-cache Whisper base.en model so transcription is instant on first use
    log_info "Pre-caching faster-whisper base.en speech model..."
    uv run --with faster-whisper --with ctranslate2 python3 -c "
import sys
try:
    from faster_whisper import WhisperModel
    WhisperModel('base.en', device='cpu', compute_type='int8')
    print('  -> Whisper base.en model ready.')
except Exception as e:
    print('  -> Whisper pre-cache skipped or completed:', e)
" || true

    log_success "Python dependencies and Whisper models pre-cached."
}

# ------------------------------------------------------------------------------
# 7. Environment & Directories Setup
# ------------------------------------------------------------------------------
setup_workspace() {
    log_info "Setting up directories and configuration..."

    mkdir -p media/youtube media/soundcloud

    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            cp .env.example .env
            log_success "Created .env configuration from .env.example"
        else
            cat <<EOF > .env
TITLE="PlaylistDL"
EMAIL="admin@PlaylistDL.local"
DB="media/playlist_metadata.duckdb"
FILE_DIR="media/"
OLLAMA_HOST="http://localhost:11434"
OLLAMA_MODEL="qwen2.5:7b"
OLLAMA_EMBED_MODEL="nomic-embed-text"
WHISPER_MODEL="base.en"
EOF
            log_success "Created default .env configuration."
        fi
    fi

    # Make executable
    chmod +x install.sh DLplaylist.sh app.py playlistDL.py transcribe.py clean_db.py downloader.py 2>/dev/null || true
    log_success "File permissions updated."
}

# ------------------------------------------------------------------------------
# Main Execution
# ------------------------------------------------------------------------------
main() {
    install_system_packages
    install_uv
    install_deno
    install_ollama
    pull_ollama_models
    setup_workspace
    prewarm_python_and_whisper

    echo ""
    echo -e "${GREEN}${BOLD}==========================================================${NC}"
    echo -e "${GREEN}${BOLD}           PlaylistDL Installation Completed!             ${NC}"
    echo -e "${GREEN}${BOLD}==========================================================${NC}"
    echo ""
    echo -e "You can now run any of the following:"
    echo ""
    echo -e "  ${BOLD}1. Start the Web Study Hall:${NC}"
    echo -e "     ${CYAN}uv run app.py${NC}"
    echo -e "     Open ${BOLD}http://localhost:5000${NC} in your browser."
    echo ""
    echo -e "  ${BOLD}2. Download a Playlist / Media URL (CLI):${NC}"
    echo -e "     ${CYAN}uv run playlistDL.py <URL>${NC}"
    echo ""
    echo -e "  ${BOLD}3. Transcribe & Embed Audio for AI Chat (CLI):${NC}"
    echo -e "     ${CYAN}uv run transcribe.py${NC}"
    echo ""
    echo -e "  ${BOLD}4. Launch Rofi GUI Selector:${NC}"
    echo -e "     ${CYAN}./DLplaylist.sh${NC}"
    echo ""
}

main "$@"
