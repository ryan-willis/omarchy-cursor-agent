#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${CURSOR_USAGE_REPO:-https://github.com/ryan-willis/omarchy-cursor-agent.git}"
PLUGIN_ID="cursor.usage"
plugins_dir="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins"
plugin_dir="$plugins_dir/$PLUGIN_ID"
export PATH="$HOME/.local/bin:$PATH"

fail() {
  echo "install: $*" >&2
  exit 1
}

have_cursor_login() {
  [[ -f ${XDG_CONFIG_HOME:-$HOME/.config}/cursor/auth.json ]] && return 0
  [[ -f ${XDG_CONFIG_HOME:-$HOME/.config}/Cursor/User/globalStorage/state.vscdb ]]
}

checkout_dir() {
  local source="${BASH_SOURCE[0]:-}"
  local dir
  [[ -n $source && $source != bash ]] || return 1
  dir="$(cd "$(dirname "$source")" && pwd)"
  [[ -f $dir/manifest.json ]] || return 1
  printf '%s\n' "$dir"
}

install_cli() {
  echo "Installing Cursor Agent from https://cursor.com/install"
  curl -fsS https://cursor.com/install | bash
  export PATH="$HOME/.local/bin:$PATH"
  command -v cursor-agent >/dev/null || fail "Cursor Agent installed, but cursor-agent is not on PATH. Open a new terminal and run this again."
}

install_plugin() {
  if [[ -e $plugin_dir || -L $plugin_dir ]]; then
    omarchy-shell shell rescanPlugins >/dev/null
    omarchy plugin enable "$PLUGIN_ID"
    return
  fi

  local checkout=""
  checkout="$(checkout_dir)" || checkout=""
  if [[ -n $checkout ]]; then
    mkdir -p "$plugins_dir"
    ln -s "$checkout" "$plugin_dir"
    omarchy-shell shell rescanPlugins >/dev/null
    omarchy plugin enable "$PLUGIN_ID"
    return
  fi

  [[ $REPO_URL == https://github.com/*/* ]] || fail "CURSOR_USAGE_REPO must be a GitHub repository URL."
  omarchy plugin add "$REPO_URL" --enable --yes
}

command -v omarchy >/dev/null || fail "This installs on Omarchy."

if command -v cursor-agent >/dev/null; then
  echo "Cursor Agent is already installed."
elif have_cursor_login; then
  echo "A Cursor desktop login is already on this machine."
else
  install_cli
fi

echo "Enabling the Cursor usage plugin. It runs inside the Omarchy shell."
install_plugin

echo
echo "Open the Agents panel. Cursor shows Grok/Cursor and Other models as separate meters."
if ! have_cursor_login; then
  echo "Sign in first by running: cursor-agent"
fi
