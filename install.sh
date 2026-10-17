#!/usr/bin/env bash
# install.sh — install the `text-lint` CLI (Python >= 3.12)
# Works both from a local clone (./install.sh) and piped from curl:
#   curl -fsSL https://git.hyperide.ai/ultrabricks/text-lint/raw/branch/main/install.sh | bash
#
# text-lint wraps two engines: Vale (a Go binary, installed separately: `brew install vale`)
# and aiw-ru (a Python package, pulled in as a dependency). The installer is PIPX-FIRST, then
# uv tool (isolated venv with aiw-ru inside + `text-lint` and the deprecated `slop-check` on
# PATH). Without either it falls back to symlinks of bin/text-lint and bin/slop-check, and
# then aiw-ru has to be on PATH on its own.
set -euo pipefail

# ── identity ──────────────────────────────────────────────────────────────────
TOOL="text-lint"
REPO="text-lint"
FORGE="https://git.hyperide.ai/ultrabricks"
ENTRY="bin/text-lint"   # path inside repo root
OLD_TOOL="slop-check"   # deprecated name, a shim that runs `text-lint check`
CLONE_BASE="${XDG_DATA_HOME:-$HOME/.local/share}"
EXPECT_URL="$FORGE/$REPO.git"

# 0.1 shipped as the distribution hyper-slop-check, whose venv owns the `slop-check` command
# that hyper-text-lint now provides as a shim. The new distribution is installed first, and
# the old one is removed only after that succeeded, so a failed install leaves 0.1 working.
OLD_DIST="hyper-slop-check"

# Run the new distribution's install command "$@". --force lets pipx and uv take over entry
# points another venv owns (`slop-check`).
_install_replacement() {
  if ! "$@"; then
    echo "  ERROR: '$*' failed. $TOOL is not installed; the old $OLD_DIST install, if any, is" >&2
    echo "         left in place, so '$OLD_TOOL' keeps working. Fix the error above and re-run." >&2
    exit 1
  fi
}

# _old_dist_in pipx|uv: does that manager hold the old distribution? The list is read whole
# first: `grep -q` on a pipe can exit early and, under pipefail, fail the test.
_old_dist_in() {
  local listed
  command -v "$1" >/dev/null 2>&1 || return 1
  if [[ "$1" == pipx ]]; then
    listed="$(pipx list --short 2>/dev/null || true)"
  else
    listed="$(uv tool list 2>/dev/null || true)"
  fi
  grep -q "^$OLD_DIST " <<<"$listed"
}

# _retire_old_dist BIN_DIR INSTALL...: remove the old distribution from pipx and uv, after the
# new install command INSTALL succeeded. Uninstalling it can take the `slop-check` entry point
# along, which now belongs to the new install; when an uninstall ran (even a failed one) and
# the shim is gone (see _entry_present), INSTALL runs once more to put it back.
_retire_old_dist() {
  local bin_dir="$1"; shift
  local manager attempted=""
  local -a uninstall
  for manager in pipx uv; do
    _old_dist_in "$manager" || continue
    if [[ "$manager" == pipx ]]; then uninstall=(pipx uninstall "$OLD_DIST"); else uninstall=(uv tool uninstall "$OLD_DIST"); fi
    echo "$TOOL: removing the old $OLD_DIST $manager install"
    attempted=1
    if ! "${uninstall[@]}"; then
      echo "  WARNING: '${uninstall[*]}' failed; $TOOL is installed, but remove $OLD_DIST" >&2
      echo "           by hand with that command." >&2
    fi
  done
  [[ -n "$attempted" ]] || return 0
  _entry_present "$OLD_TOOL" "$bin_dir" && return 0
  echo "$TOOL: restoring the $OLD_TOOL shim the old uninstall removed"
  if ! "$@"; then
    # A failed --force install may have taken down the tool it was rebuilding.
    if _entry_present "$TOOL" "$bin_dir"; then
      echo "  ERROR: '$*' failed while restoring '$OLD_TOOL'. $TOOL is installed and $OLD_DIST" >&2
      echo "         is removed, but '$OLD_TOOL' is missing: re-run this installer." >&2
    else
      echo "  ERROR: '$*' failed while restoring '$OLD_TOOL'. $OLD_DIST is removed, and" >&2
      echo "         '$TOOL' and '$OLD_TOOL' are both missing now: re-run this installer." >&2
    fi
    exit 1
  fi
  if ! _entry_present "$OLD_TOOL" "$bin_dir"; then
    echo "  WARNING: '$*' succeeded but '$OLD_TOOL' is still missing. $TOOL is installed;" >&2
    echo "           run '$*' once more, or call '$TOOL check' instead of '$OLD_TOOL'." >&2
  fi
}

# _entry_present NAME BIN_DIR: the entry point NAME exists in BIN_DIR (the installer's guess of
# the bin dir, "" for none) or on PATH, as $TOOL is looked up after the install: the manager
# may write entry points elsewhere (UV_TOOL_BIN_DIR, pipx's own config).
_entry_present() {
  [[ -n "$2" && -e "$2/$1" ]] || command -v "$1" >/dev/null 2>&1
}

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
      INSTALL_CMD=(uv tool install --force --editable "$_src")
    else
      INSTALL_CMD=(uv tool install --force "git+${EXPECT_URL%.git}")
    fi
    _install_replacement "${INSTALL_CMD[@]}"
    # uv's bin dir is not known here: "" looks entry points up on PATH only.
    _retire_old_dist "" "${INSTALL_CMD[@]}"
    command -v vale >/dev/null 2>&1 || echo "  NOTE: install Vale too: winget install errata-ai.Vale"
    "$TOOL" install-skill || echo "  WARNING: '$TOOL install-skill' failed — re-run it once $TOOL is on PATH."
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
  INSTALL_CMD=(pipx install --force --editable "$SRC")
elif command -v uv >/dev/null 2>&1; then
  INSTALL_MODE="uv tool"
  INSTALL_CMD=(uv tool install --force --editable "$SRC")
fi
if [[ -n "$INSTALL_MODE" ]]; then
  echo "$TOOL: installing via $INSTALL_MODE (isolated venv with aiw-ru)"
  _install_replacement "${INSTALL_CMD[@]}"
  _retire_old_dist "$BIN" "${INSTALL_CMD[@]}"

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
  chmod +x "$ENTRY_PATH" "$SRC/bin/$OLD_TOOL"
  ln -sfn "$ENTRY_PATH" "$BIN/$TOOL"
  ln -sfn "$SRC/bin/$OLD_TOOL" "$BIN/$OLD_TOOL"
  TOOL_BIN="$BIN/$TOOL"
  echo "$TOOL: symlinked $BIN/$TOOL -> $ENTRY_PATH (and $BIN/$OLD_TOOL)"
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

# ── advertise to agents ───────────────────────────────────────────────────────
# The blurb is also rig's "installed" marker: without it every `rig apply` reruns this script.
if ! "$TOOL_BIN" install-skill; then
  echo "  WARNING: '$TOOL install-skill' failed — $TOOL is installed but agents may not"
  echo "           auto-discover it. Re-run '$TOOL install-skill' manually to fix."
fi

echo ""
echo "  $TOOL is installed (via $INSTALL_MODE)."
echo "  Usage: $TOOL check --context issue body.md   |   echo text | $TOOL check   |   $TOOL check --help"
echo ""
