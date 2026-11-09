# Security

## Scope

This is a local DSH plugin. It has no telemetry endpoint, no uploader and no background network
client. It reads its own package files, writes state/logs under `$DSH_HOME/coordination/`, and
calls the host's public `pluginManager`/`subprocess`/`webServer` services when the user asks.

## Boundaries

- Enforcement covers the plugin's controlled paths only: prompt contract injection, the
  `coordination` tool and guard, the human-only install/verify routes, and Python gate calls.
  Host tools outside the plugin, direct shell/network, other plugins' private paths and the
  model's own reasoning are outside it. No OS-level sandbox or host-wide interception is claimed.
- Extension installation is user-confirmed, installs **disabled**, and activation requires a
  reported identity plus a declared probe. The plugin never installs, updates or removes a
  third-party plugin on its own.
- Version activation requires the user's explicit verify + confirm; the plugin never updates itself.

## Secrets and logs

- Credentials are never read by this plugin. It does not copy provider keys or upstream config.
- Logs redact keys, tokens, cookies, bearer headers, user-home paths and emails; they do not store
  prompts, messages, file contents or full request/response bodies. Logs stay local and outside git.
- The HTTP control plane binds to the host's loopback web server and requires a per-process CSRF
  token plus an explicit `confirm` for mutations. It is not a model tool.

## Reporting

Open a private security report in the repository (do not paste live credentials). Include the DSH
version, plugin version, profile, reproduction steps and the relevant local log line with secrets
already redacted. Desktop is unverified in v3.0.0; do not report a desktop claim as supported.
