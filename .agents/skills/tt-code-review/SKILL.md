---
name: tt-code-review
description: Review TrustTunnel code changes for correctness, regressions, architecture violations, security problems, missing tests, operational risks, and maintainability. Use for diffs, branches, pull requests, or pre-commit review. Review first; do not modify code unless the user explicitly asks for fixes.
---

# TT Code Review

Always combine with:

- `tt-project-context`;
- `tt-security`;
- `tt-testing`.

Review the actual diff and relevant surrounding code.

Do not review only the changed lines when correctness depends on callers, data models, migrations, configuration, Redis adapters, or PostgreSQL persistence.

## Priority

Focus on defects that can produce:

- security exposure;
- incorrect authorization;
- data corruption;
- broken configuration;
- service outage;
- inconsistent Control Plane/Data Plane state;
- failed provisioning;
- incompatible API/schema changes;
- incorrect async behavior;
- missing critical validation.

Avoid flooding the review with stylistic preferences already handled by formatters/linters.

## Architecture checks

Look for:

- CP/DP responsibility leakage;
- business logic in transport handlers;
- hidden component coupling;
- inappropriate synchronous dependency;
- unnecessary new infrastructure;
- speculative Kubernetes complexity;
- duplicated source-of-truth logic;
- Redis being used as authoritative durable storage;
- direct Redis coupling where project abstractions should be used;
- Kafka or another broker introduced without an explicit architectural decision.

## Security checks

Pay special attention to:

- SSH credentials;
- API authentication;
- object-level authorization;
- viewer/admin separation;
- logging of secrets;
- Redis job/event/notification payloads;
- generated client configuration;
- unsafe defaults.

## Reliability checks

For async/distributed changes inspect:

- retries;
- idempotency;
- duplicate execution/delivery;
- partial failure;
- ordering assumptions;
- timeout behavior;
- state transitions;
- recovery after Redis restart or temporary unavailability;
- consistency with PostgreSQL durable state.

## Tests

Check whether tests demonstrate the intended behavior and important failure paths.

Do not treat test presence alone as evidence of correctness.

## Review output

Lead with findings.

For each finding include:

- severity;
- file/location;
- concrete failure scenario;
- why it matters;
- smallest reasonable fix.

Use severity only for engineering impact:

- critical: likely severe security/data/system failure;
- high: serious correctness or security defect;
- medium: meaningful defect or operational risk;
- low: limited defect or maintainability issue.

After findings, include:

- open questions;
- test gaps;
- short overall change summary.

If no material findings exist, say so explicitly and mention remaining test/verification limitations.
