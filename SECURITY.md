# Security Policy

## Supported Versions

Security fixes are applied to the latest released minor line.

| Version | Supported |
| --- | --- |
| `0.17.x` | Yes |
| `< 0.17.0` | No |

## Reporting a Vulnerability

If you discover a security issue in Notion Agent Labbook, please report it privately by email:

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
- keyring or 1Password storage and retrieval of the Notion Internal Integration secret
- MCP server tool behavior or local state access
- Notion API credential handling

## Security Model

Notion Agent Labbook is now a local MCP server with a direct Notion API integration. Its current security model is:

- `.labbook/session.json` should not contain the Notion integration secret. It stores only metadata such as token source and bot information.
- Long-lived Notion secrets should live in the configured local storage backend, currently system keychain or 1Password, or in the process environment through `NOTION_AGENT_LABBOOK_TOKEN`.
- `.labbook/bindings.json` stores only project binding metadata.
- `notion_status` and `notion-agent-labbook doctor` are the preferred non-secret inspection paths.
- The MCP server can return the Notion secret through `notion_get_api_context`, so clients should call that tool only when they are ready to use the official Notion API and should treat the returned secret as sensitive.

## Out of Scope

The following are generally not treated as product vulnerabilities by themselves:

- a workstation compromise that already gives an attacker access to the local keychain, 1Password session, or shell environment
- intentionally forcing insecure local workflows such as committing `.labbook/` into source control
- storing the integration secret in plaintext files outside the project's documented setup

## Hardening Notes

- Prefer the local system keychain or 1Password over plaintext project files.
- Prefer `notion_status` or `doctor` over `notion_get_api_context` when you only need to verify whether the project is configured.
- If you believe a Notion secret was exposed, rotate the Internal Integration secret in Notion and update the local configuration.
- Keep `.labbook/` out of version control.

## Response

I will try to acknowledge reports promptly and follow up with remediation or mitigation guidance as soon as practical. When a report is confirmed, the usual goal is:

- private acknowledgement first
- a fix or mitigation plan before public disclosure
- coordinated release notes once the patch is available
