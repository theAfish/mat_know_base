# Security model

MKB defaults to trusted, single-user, localhost-only operation. Development and
local modes reject non-loopback API binds. PostgreSQL, MinIO, and the MinIO
console are also published on loopback only by the supplied Compose file.

## Authentication and roles

Remote binding is permitted only in `production` mode with authentication,
non-default infrastructure credentials, INFO-or-higher logging, exact CORS
origins, and at least one bearer token. Configure tokens only through `.env`:

```dotenv
MKB_DEPLOYMENT_MODE=production
MKB_API_HOST=0.0.0.0
MKB_AUTHENTICATION_ENABLED=true
MKB_AUTH_TOKENS={"<random-token-of-at-least-32-characters>":"admin"}
MKB_CORS_ORIGINS=["https://mkb.example.org"]
```

Generate an opaque token with `openssl rand -hex 32`. The token map supports:

| Role | Permissions |
| --- | --- |
| `reader` | Read API data and source assets |
| `editor` | Read, mutate, and start jobs |
| `admin` | All permissions, including deletes, settings, and code uploads |

Send the token as `Authorization: Bearer <token>`. The React client reads a token
from browser `sessionStorage`; it can be set for the current tab with:

```js
sessionStorage.setItem('mkb_api_token', '<token>')
```

Bearer authentication uses an explicit header rather than cookies, so browser
CSRF protections are not applicable. Exact CORS origins are still required.
Liveness and readiness paths are public; every `/api` data route is protected
when authentication is enabled.

## Abuse and executable-content controls

Authentication failures, uploads, assistant calls, and job starts use per-process
sliding-window limits. Deployments with multiple API processes should additionally
enforce shared limits at a reverse proxy or gateway.

Uploaded Python post-processors are disabled by default and prohibited in
production. `MKB_ALLOW_UPLOADED_PYTHON=true` is a trusted-local administrator
escape hatch, not a sandbox: enabled scripts retain host filesystem and network
access. Do not enable it for untrusted content.

Upload and archive limits are configured through the `MKB_UPLOAD_*` and
`MKB_ARCHIVE_*` environment variables documented in `.env.example`.

## Current limitations

- Bearer tokens are static and must be rotated through `.env` plus a restart.
- Rate-limit state is not shared across API processes.
- Uploaded Python does not yet have a constrained worker/container runtime.
- Audit records and soft-delete recovery are still pending.
