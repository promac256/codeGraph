# @recon/web — UiPath Coded App (React + Vite)

The customer-facing SPA, deployed as a **UiPath Coded App** (decided in the blueprint).

- **Hosting:** static bundle served at `https://<org>.uipath.host/<app>` inside UiPath
  governance, packaged in the Solution. Automation-Cloud-only; no custom domain.
- **Multi-tenancy:** one Coded App URL for all tenants — tenant identity comes from the
  authenticated principal (SSO); RLS scopes every query. No per-tenant subdomain.
- **Branding:** in-app theming (fixed domain), per-tenant logo/theme in-UI.
- **Server-side work:** the heavy matching engine is a separate **External Application**
  (`@recon/backend`) the Coded App calls; UiPath serverless robots handle inline automation.

## Scaffolding (to do)

Scaffold with UiPath **Studio Web** + the Coded Apps CLI (pin `>= 0.1.21`, codedapp tool
`>= 0.1.14`), using React + Vite + TypeScript, importing shared types from `@recon/shared`
and calling UiPath via `@uipath/uipath-typescript`. Package into the Solution alongside the
backend's automations, Data Service entities, queues, and Action Center apps.
