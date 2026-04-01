#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/e10nMa2k/cc-mini.git"
INSTALL_DIR="${CC_MINI_INSTALL_DIR:-$HOME/.cc-mini}"
BRANCH="${CC_MINI_BRANCH:-main}"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; RESET='\033[0m'
info()    { printf "${CYAN}[cc-mini]${RESET} %s\n" "$*"; }
success() { printf "${GREEN}[cc-mini]${RESET} ${BOLD}%s${RESET}\n" "$*"; }
warn()    { printf "${YELLOW}[cc-mini]${RESET} %s\n" "$*" >&2; }
die()     { printf "${RED}[cc-mini] ERROR:${RESET} %s\n" "$*" >&2; exit 1; }

# ── Detect Python 3.11+ ───────────────────────────────────────────────────────
find_python() {
    for cmd in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null) || continue
            # (3, 11) style tuple comparison
            if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    printf "\n${BOLD}╔══════════════════════════════════════════╗${RESET}\n"
    printf   "${BOLD}║        cc-mini  installer                ║${RESET}\n"
    printf   "${BOLD}╚══════════════════════════════════════════╝${RESET}\n\n"

    # 1. Check dependencies
    command -v git &>/dev/null || die "git is required but not found. Please install git first."

    PYTHON=$(find_python) || die "Python 3.11+ is required but not found.
  Install it with:  sudo apt install python3.11   (Debian/Ubuntu)
                    brew install python@3.11       (macOS)"

    PY_VER=$("$PYTHON" -c 'import sys; v=sys.version_info; print(f"{v.major}.{v.minor}.{v.micro}")')
    info "Using Python ${PY_VER} (${PYTHON})"

    # 2. Clone or update repo
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Updating existing installation at ${INSTALL_DIR} ..."
        git -C "$INSTALL_DIR" fetch --quiet origin
        git -C "$INSTALL_DIR" reset --hard "origin/${BRANCH}" --quiet
    else
        info "Cloning cc-mini into ${INSTALL_DIR} ..."
        rm -rf "$INSTALL_DIR"
        git clone --depth 1 --branch "$BRANCH" "$REPO" "$INSTALL_DIR" --quiet
    fi

    # 3. Install into a dedicated venv inside INSTALL_DIR
    VENV_DIR="$INSTALL_DIR/.venv"
    if [[ ! -d "$VENV_DIR" ]]; then
        info "Creating virtual environment ..."
        "$PYTHON" -m venv "$VENV_DIR"
    fi

    info "Installing dependencies ..."
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -e "$INSTALL_DIR"

    # 4. Create a launcher script in ~/.local/bin (no sudo needed)
    BIN_DIR="${CC_MINI_BIN_DIR:-$HOME/.local/bin}"
    mkdir -p "$BIN_DIR"
    LAUNCHER="$BIN_DIR/cc-mini"

    cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "${VENV_DIR}/bin/cc-mini" "\$@"
EOF
    chmod +x "$LAUNCHER"

    # 5. PATH advice
    printf "\n"
    success "cc-mini installed successfully!"
    printf "\n"

    if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
        warn "${BIN_DIR} is not in your PATH."
        printf "  Add it by running one of:\n\n"
        printf "    ${BOLD}echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc${RESET}\n"
        printf "    ${BOLD}echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc  && source ~/.zshrc${RESET}\n\n"
    else
        printf "  Run:  ${BOLD}cc-mini${RESET}\n\n"
    fi

    printf "  Set your API key first:\n"
    printf "    ${BOLD}export ANTHROPIC_API_KEY=sk-ant-...${RESET}\n\n"
    printf "  Installed to:  ${CYAN}${INSTALL_DIR}${RESET}\n"
    printf "  Launcher:      ${CYAN}${LAUNCHER}${RESET}\n\n"
}

main "$@"
