---
name: tt-frontend
description: Implement and review the TrustTunnel administration and client-facing web interfaces. Use for UI flows, API integration, admin/viewer experiences, client self-service configuration flows, status views, notifications, forms, accessibility, and frontend testing.
---

# TT Frontend

Always combine with:

- `tt-project-context`;
- `tt-security` for auth/session/sensitive-data behavior;
- `tt-testing` for implementation work.

## Product principle

The primary UX should remain understandable to non-technical users.

Advanced infrastructure details should not dominate the default user flow.

Expose advanced controls only when they are useful and safe.

## Role behavior

UI may adapt to admin/viewer permissions, but frontend permission checks are usability controls only.

Backend authorization remains authoritative.

Do not imply that hiding a button provides security.

## Client self-service

Make device and per-server configuration lifecycles understandable:

- available configurations;
- device quota usage and device_limit returned by the backend;
- creation state;
- active/revoked/failed status;
- download or enrollment action where applicable;
- clear destructive-action confirmation.

Respect the user's device_limit provided by the backend. Do not hard-code 3 or treat per-server configurations as additional devices.

Handle backend quota conflicts correctly rather than assuming frontend state is current.

## Long-running operations

Provisioning and other asynchronous operations need explicit states such as:

- pending;
- running;
- completed;
- failed.

Do not fake success while backend work is still in progress.

If job or notification status is delivered through Redis-backed backend infrastructure, the frontend should consume only the supported API or realtime interface, never connect to Redis directly.

Provide actionable failure messages without exposing infrastructure secrets.

## Sensitive data

Do not persist sensitive generated configuration data in browser storage unless explicitly required and reviewed.

Do not log tokens, passwords, private keys, or configuration secrets to the browser console.

## UI quality

Follow existing project components and styling before creating new primitives.

Check:

- loading states;
- empty states;
- error states;
- disabled states;
- responsive behavior;
- keyboard accessibility;
- meaningful labels;
- destructive-action confirmation.

## Completion

Run the repository's relevant:

- type checks;
- linting;
- unit/component tests;
- integration/e2e tests where available.

For meaningful UI changes, describe the user flow in the completion report.
