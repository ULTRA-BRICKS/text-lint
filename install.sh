#!/usr/bin/env bash
# install.sh — install the `slop-check` CLI (Python >= 3.12)
# Works both from a local clone (./install.sh) and piped from curl:
#   curl -fsSL https://git.hyperide.ai/ultrabricks/slop-cli/raw/branch/main/install.sh | bash
#
# slop-check wraps two engines: Vale (a Go binary, installed separately: `brew install vale`)
# and aiw-ru (a Python package, pulled in as a dependency). The installer is PIPX-FIRST, then
# uv tool (isolated venv with aiw-ru inside + `slop-check` on PATH). Without either it falls
# back to a symlink of bin/slop-check, and then aiw-ru has to be on PATH on its own.
set -euo pipefail

# ── identity ──────────────────────────────────────────────────────────────────
TOOL="slop-check"
REPO="slop-cli"
FORGE="https://git.hyperide.ai/ultrabricks"
ENTRY="bin/slop-check"   # path inside repo root
CLONE_BASE="${XDG_DATA_HOME:-$HOME/.local/share}"
EXPECT_URL="$FORGE/$REPO.git"

# ── Windows (Cygwin / MSYS2 / Git Bash) ───────────────────────────────────────
# There python, git and uv are native Windows programs: a symlink to the entry script cannot
# run. `uv tool install` puts a real <tool>.exe into %USERPROFILE%\.local\bin, which IS on PATH
# in every Windows shell, so on Windows that is the whole install.
case "$(uname -s)" in
  CYGWIN*|MINGW*|MSYS*)
    if ! command -v uv >/dev/null 2>&1; then
      echo "ERROR: on Windows $TOOL installs through uv, and uv is not on PATH." >&2
      echo "       Install it:  winget install astral-sh.uv" >&2
      exit 1
    fi
    _here=""
    if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "bash" ]]; then
      _here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    fi
    if [[ -n "$_here" && -f "$_here/$ENTRY" ]]; then
      _src="$_here"; command -v cygpath >/dev/null 2>&1 && _src="$(cygpath -w "$_here")"
      uv tool install --force --editable "$_src"
    else
      uv tool install --force "git+${EXPECT_URL%.git}"
    fi
    command -v vale >/dev/null 2>&1 || echo "  NOTE: install Vale too: winget install errata-ai.Vale"
    echo "  $TOOL is installed (uv tool)."
    exit 0
    ;;
esac

# Compared as HOST + PATH, never as a substring: an origin that merely contains "hyperide.ai"
# (git.hyperide.ai.evil.example, evil.example/git.hyperide.ai/…) must not pass for the canonical
# repo and get pulled + installed.
_url_host_path() {  # https / ssh / scp-style git URL -> "host path" (user@, :port, .git, trailing / dropped)
  local _u _hp _path _host
  _u=${1%/}; _u=${_u%.git}
  case "$_u" in
    https://*|ssh://*) _u=${_u#*://}; _hp=${_u%%/*}; _path=${_u#*/} ;;
    *@*:*) _hp=${_u%%:*}; _path=${_u#*:} ;;
    *) return 1 ;;
  esac
  [ "$_path" != "$_u" ] || return 1
  case "$_hp" in *[!A-Za-z0-9._:@%~-]*) return 1 ;; esac
  _host=${_hp##*@}; _host=${_host%%:*}
  printf '%s %s\n' "$_host" "$_path"
}
_origin_is_canonical() {
  [ "$(_url_host_path "$1")" = "git.hyperide.ai ultrabricks/$REPO" ]
}

# ── locate source dir ─────────────────────────────────────────────────────────
_script_dir=""
if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "bash" ]]; then
  _script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -n "$_script_dir" && -f "$_script_dir/$ENTRY" ]]; then
  SRC="$_script_dir"
  echo "$TOOL: using local clone at $SRC"
else
  mkdir -p "$CLONE_BASE"
  CLONE_DIR="$CLONE_BASE/$REPO"
  if [[ -d "$CLONE_DIR/.git" ]]; then
    actual_url="$(git -C "$CLONE_DIR" remote get-url origin 2>/dev/null || echo "")"
    if ! _origin_is_canonical "$actual_url"; then
      echo "ERROR: $CLONE_DIR exists but its origin is '$actual_url', not $EXPECT_URL." >&2
      echo "       Remove that directory or fix its remote, then re-run." >&2
      exit 1
    fi
    echo "$TOOL: updating existing clone at $CLONE_DIR"
    _branch="$(git -C "$CLONE_DIR" symbolic-ref --quiet --short HEAD || echo HEAD)"
    git -C "$CLONE_DIR" pull --ff-only origin "$_branch"
  else
    echo "$TOOL: cloning $EXPECT_URL into $CLONE_DIR"
    git clone "$EXPECT_URL" "$CLONE_DIR"
  fi
  SRC="$CLONE_DIR"
fi

# ── bin dir ───────────────────────────────────────────────────────────────────
BIN="${PIPX_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN"

if [[ ":$PATH:" != *":$BIN:"* ]]; then
  echo ""
  echo "  NOTE: $BIN is not on your PATH. Add it to ~/.bashrc or ~/.zshrc:"
  echo "    export PATH=\"$BIN:\$PATH\""
  echo ""
fi

# ── install ───────────────────────────────────────────────────────────────────
TOOL_BIN=""
INSTALL_MODE=""
if command -v pipx >/dev/null 2>&1; then
  INSTALL_MODE="pipx"
  echo "$TOOL: installing via pipx (isolated venv with aiw-ru)"
  pipx install --force --editable "$SRC"
elif command -v uv >/dev/null 2>&1; then
  INSTALL_MODE="uv tool"
  echo "$TOOL: installing via uv tool (isolated venv with aiw-ru)"
  uv tool install --force --editable "$SRC"
fi

if [[ -n "$INSTALL_MODE" ]]; then
  if [[ -x "$BIN/$TOOL" ]]; then
    TOOL_BIN="$BIN/$TOOL"
  else
    TOOL_BIN="$(command -v "$TOOL" 2>/dev/null || true)"
  fi
  if [[ -z "$TOOL_BIN" || ! -x "$TOOL_BIN" ]]; then
    echo "  ERROR: $INSTALL_MODE install succeeded but '$TOOL' is not on PATH (check $BIN)" >&2
    exit 1
  fi
  echo "$TOOL: $INSTALL_MODE installed $TOOL at $TOOL_BIN"
else
  INSTALL_MODE="symlink"
  echo ""
  echo "  NOTE: neither pipx nor uv found — falling back to a symlink."
  echo "  aiw-ru then has to be on PATH by itself:  pip install --user 'aiw-ru>=2.5,<3'"
  echo ""
  ENTRY_PATH="$SRC/$ENTRY"
  chmod +x "$ENTRY_PATH"
  ln -sfn "$ENTRY_PATH" "$BIN/$TOOL"
  TOOL_BIN="$BIN/$TOOL"
  echo "$TOOL: symlinked $BIN/$TOOL -> $ENTRY_PATH"
fi

# ── engines ───────────────────────────────────────────────────────────────────
if ! command -v vale >/dev/null 2>&1; then
  echo ""
  echo "  NOTE: Vale (the English engine) is not on PATH. Install it:"
  echo "    brew install vale        # or: go install github.com/vale-cli/vale/v3/cmd/vale@v3.24.0"
  echo ""
fi

# ── shadow check ──────────────────────────────────────────────────────────────
RESOLVED="$(command -v "$TOOL" 2>/dev/null || true)"
if [[ -n "$RESOLVED" && "$RESOLVED" != "$TOOL_BIN" ]]; then
  echo "  WARNING: another '$TOOL' shadows our install on PATH:" >&2
  echo "      installed: $TOOL_BIN" >&2
  echo "      resolves to: $RESOLVED" >&2
fi

if [[ -z "$RESOLVED" ]]; then
  echo "  WARNING: $TOOL is installed at $TOOL_BIN, but $BIN is not on PATH." >&2
  echo "  Fix it:  export PATH=\"$BIN:\$PATH\"" >&2
  exit 1
fi

echo ""
echo "  $TOOL is installed (via $INSTALL_MODE)."
echo "  Usage: $TOOL --context issue body.md   |   echo text | $TOOL   |   $TOOL --help"
echo ""
