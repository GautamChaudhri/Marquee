# Security policy

## Supported versions

Marquee is pre-1.0 and does not yet have a stable support line. Security fixes are made on `main`
and included in the next release; older development snapshots are not maintained.

## Reporting a vulnerability

Please do not publish exploit details, credentials, private media paths, or other sensitive evidence
in a public issue.

Use GitHub's **Security → Report a vulnerability** form when private vulnerability reporting is
available. If that form is unavailable, contact the repository owner through the
[GitHub profile](https://github.com/GautamChaudhri) and ask for a private reporting channel without
including sensitive details in the first message.

Include the affected version or commit, deployment mode, impact, reproduction steps, and any safe
mitigation you already identified. You should receive an acknowledgement within seven days.

## Deployment boundary

Marquee is intended for a trusted self-hosted network. Keep the API and PostgreSQL ports off the
public internet, use a long random API key, protect `.env` and backup files, and expose the web UI
through an authenticated reverse proxy appropriate for your environment.
