# Cursor usage

Omarchy service plugin. It does not add a bar icon, and it does not install the Cursor Agent CLI by itself.

It writes a Cursor plan record to `~/.local/state/omarchy/agents/usage/cursor.json`. The Agents panel already watches that directory, so Cursor shows up beside the other subscriptions.

The two included pools are separate meters:

- **Grok/Cursor** — Auto, Composer, and Cursor Grok
- **Other models** — named and API models

The plan name, such as Pro+, comes from the same account summary. On-demand spend is not a meter. Omarchy draws these meters the same way it draws Claude, Codex, and Grok: the time left is at the end of each meter, and the percent is on hover.

## Quick start

You need Omarchy, and a Cursor login. The desktop app is enough. If neither the app nor `cursor-agent` is present, the installer runs Cursor's own installer from <https://cursor.com/install>.

```sh
curl -fsSL https://raw.githubusercontent.com/ryan-willis/omarchy-cursor-agent/main/install.sh | bash
```

The script is unsandboxed, like every Omarchy plugin. Read it before piping it. It does three things:

1. Leaves `cursor-agent` alone when it, or a Cursor desktop login, is already there.
2. Otherwise installs Cursor Agent with `curl -fsS https://cursor.com/install | bash`.
3. Enables this plugin with `omarchy plugin add … --enable --yes`.

Sign in once if you have not already:

```sh
cursor-agent
```

Open the Agents panel. The first collect also asks that panel to rescan. After that, usage refreshes every five minutes while the plugin is enabled.

From a checkout of this repo, `./install.sh` links that checkout into `~/.config/omarchy/plugins/cursor.usage` instead of cloning it again.

## Install by hand

```sh
curl -fsS https://cursor.com/install | bash
omarchy plugin add https://github.com/ryan-willis/omarchy-cursor-agent.git --enable --yes
cursor-agent
```

Skip the first line when `cursor-agent` or the Cursor app is already signed in.

The collector reads `~/.config/cursor/auth.json` first, then the desktop session in `~/.config/Cursor/User/globalStorage/state.vscdb`. `XDG_CONFIG_HOME` is honored. The session is sent only to `https://cursor.com/api/usage-summary`. Redirects are refused, and the token is not written into the usage record.

## Remove

```sh
omarchy plugin disable cursor.usage
omarchy plugin remove cursor.usage --yes
rm -f "${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/agents/usage/cursor.json"
```

That does not uninstall Cursor Agent.
