# Security Policy

## Supported Versions

Security fixes are applied to the latest released minor line.

| Version | Supported |
| --- | --- |
| `0.14.x` | Yes |
| `< 0.14.0` | No |

## Reporting a Vulnerability

If you discover a security issue in Agent Labbook, please report it privately by email:

- `binbinsh@gmail.com`

Please include:

- a description of the issue
- steps to reproduce it
- the affected version or commit when possible
- any suggested mitigation or fix if you have one

Please do not open a public issue for unpatched security vulnerabilities.

## Scope

Security reports are especially useful for issues involving:

- project-local state under `.labbook/`
- OAuth handoff and callback handling
- MCP server tool behavior or local state access
- Notion API credential handling
- reuse of shared credentials through `1Password` or `keyring`

## Security Model

Agent Labbook is a local MCP server plus an integration-specific app backend. Its current security model is:

- `.labbook/session.json` should not contain Notion access tokens or refresh tokens. It should store only a `credential_provider`, `credential_ref`, and non-secret metadata.
- Long-lived Notion tokens should live in the configured shared credential provider, currently `1Password` or the system `keyring`.
- Browser OAuth flows are split into explicit steps. `notion_status` is read-only, and `notion_finalize_pending_auth` is the step that persists a pending browser handoff.
- Headless handoff bundles are sensitive bearer artifacts until they are redeemed. Treat them like short-lived secrets and do not paste them into public logs, issue trackers, or screenshots.
- Local browser auth trusts the local machine and its localhost callback path. If the local workstation is already compromised, Agent Labbook cannot fully defend that trust boundary.

## Self-Hosting Expectations

If you self-host the backend pieces used by Agent Labbook:

- keep `NOTION_ACCESS_BROKER_SHARED_SECRET` private and rotate it if you suspect leakage
- keep all Notion `client_id` and `client_secret` values in server-side secrets, never in browser code or project-local files
- restrict browser continuation URLs and do not broaden them beyond the intended integration surfaces
- keep `.labbook/` out of version control

## Out of Scope

The following are generally not treated as product vulnerabilities by themselves:

- a workstation compromise that already gives an attacker access to the local browser, `localhost`, `1Password`, or the system keyring
- self-hosted deployments that expose secrets through logs, public repos, or misconfigured reverse proxies
- intentionally forcing insecure local workflows such as committing `.labbook/` into source control
- disclosure of a headless handoff bundle by the operator after it was shown in the browser

## Hardening Notes

- Prefer `1Password` or a properly configured system `keyring`; do not modify the code to fall back to plaintext token files.
- If you believe a Notion token was exposed, revoke the Notion integration grant and re-run OAuth.
- If you believe the shared broker secret was exposed, rotate `NOTION_ACCESS_BROKER_SHARED_SECRET` and treat existing signed state or handoff artifacts as invalid.

## Response

I will try to acknowledge reports promptly and follow up with remediation or mitigation guidance as soon as practical. When a report is confirmed, the usual goal is:

- private acknowledgement first
- a fix or mitigation plan before public disclosure
- coordinated release notes once the patch is available
