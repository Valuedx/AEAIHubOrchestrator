# AE AI Hub Orchestrator Frontend

React 19 + Vite frontend for the visual workflow builder.

## Commands

```bash
npm install
npm run dev
npm run build
npm run lint
```

## Environment

Copy `.env.example` to `.env` and adjust as needed:

```env
VITE_API_URL=http://localhost:8001
VITE_TENANT_ID=default
VITE_AUTH_MODE=dev
```

## Notes

- The shared node registry is loaded from `../shared/node_registry.json`.
- The login gate (`LoginPage`) only shows when `VITE_AUTH_MODE=oidc` (SSO) or `VITE_AUTH_MODE=local` (username/password against the backend's `users` table — LOCAL-AUTH-01). `dev` mode skips the gate entirely.
- The frontend can run without MCP, but MCP-backed tool pickers will be empty until the backend can reach an MCP server.
