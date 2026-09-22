# ADR-0014: Server management identity and SSH host trust

- Status: Accepted for TTCP-009
- Date: 2026-09-22
- Basis: architecture sections 7–9 and 23; explicit TTCP-009 decision request
- Decision authority: TTCP-009 user instruction of 2026-09-22, "resolve and document
  it now because TTCP-009 directly depends on it." This records the delegated
  implementation decision, not a claim of a separate owner review.

## Decision

Administrators obtain the complete SSH host public key through a trusted provider
console or another authenticated out-of-band channel before registering a server.
The API validates its format and stores it as the sole pinned host key. It cannot
prove that the administrator checked its origin. Never enroll a key discovered by
an unauthenticated network scan. StrictHostKeyChecking remains mandatory; there is
no TOFU, automatic acceptance, or fallback after a mismatch.

An administrator explicitly replaces the management private key and host public
key using PUT /api/servers/{id}/credentials. Audit records old/new SHA256 host-key
fingerprints, actor, target, and request ID, never private material. Replacing keys
or connection settings is prohibited while a server job is queued/running. Existing
connections must finish or be safely cancelled before rotation. Verify the new key
out of band first; a mismatch must never trigger automatic rotation.

Durable jobs contain only server identity and operation. The worker resolves the
current management identity from PostgreSQL at execution time. Only a dedicated
non-root management account and an unencrypted OpenSSH private key are admitted
by the management API. The private key is encrypted before persistence with the
existing Fernet application encryption mechanism and TTCP_AUTH_ENCRYPTION_KEY,
supplied outside PostgreSQL to both API and worker. Plaintext exists only in memory
and in the adapter's protected, short-lived files. Responses expose a fingerprint
and configuration-presence flag, never plaintext or ciphertext.

Provider/root passwords are not accepted or persisted by this API. Initial password
and key onboarding remain supported by the architecture and existing execution
boundary; implementing that temporary bootstrap flow belongs to TTCP-010. Operators
must prepare the management account/key before using these endpoints.

## Consequences

Database backup and encryption-key backup remain separate. Key recovery/rotation
must include SSH ciphertext as well as TOTP seeds. Existing metadata-only Server
rows remain readable but cannot execute jobs until credentials are configured.
Changing the target hostname invalidates cached reachability and requires the
operator to ensure the existing pin belongs to the new target (or explicitly rotate
it). A wrong pin fails closed; the API does not contact nodes during CRUD.

Reusing the existing encryption key avoids another secret-management mechanism;
worker access to that key expands its trust boundary. A future separately keyed
SSH encryption domain can reduce this scope but requires an explicit migration.
