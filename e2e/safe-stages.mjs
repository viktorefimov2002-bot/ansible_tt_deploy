// Labels are static test vocabulary. Never pass URLs, selectors, errors or payloads.
export const stages = [
  "admin-login",
  "admin-refresh",
  "server-form",
  "server-submit",
  "user-form",
  "user-submit",
  "user-select",
  "access-grant",
  "invitation-create",
  "client-exchange",
  "invitation-replay",
  "client-refresh",
  "device-create",
  "device-select",
  "server-select",
  "quota-denial",
  "worker-stop",
  "credential-queue",
  "redis-restart",
  "worker-start",
  "configuration-ready",
  "configuration-delivery",
  "configuration-download",
  "origin-denials",
  "device-revoke",
  "credential-revoke",
  "audit-check",
  "client-logout",
  "viewer-login",
  "viewer-denials",
  "admin-logout",
  "complete",
];

export function diagnostics(
  write = (line) => process.stdout.write(`${line}\n`),
) {
  let current = "admin-login";
  let started = Date.now();
  return {
    checkpoint(label) {
      if (!stages.includes(label)) throw new Error("Unknown release stage");
      write(`E2E stage=${current} elapsed_ms=${Date.now() - started}`);
      current = label;
      started = Date.now();
      write(`E2E begin=${current}`);
    },
    failure() {
      write(`E2E failed=${current} elapsed_ms=${Date.now() - started}`);
      // Discard Playwright's raw error/stack/cause, which can contain invitations,
      // fill values, response bodies, or configuration-bearing DOM excerpts.
      return new Error(`Release journey failed at stage=${current}`);
    },
  };
}
