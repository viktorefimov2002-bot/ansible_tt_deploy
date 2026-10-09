import { test } from "node:test";
import assert from "node:assert/strict";
import { diagnostics } from "./safe-stages.mjs";

test("stage failure never includes raw secret-bearing error information", () => {
  const output = [];
  const diagnostic = diagnostics((line) => output.push(line));
  diagnostic.checkpoint("client-exchange");
  const secret = "synthetic-canary-invitation-toml-token";
  let safe;
  try {
    throw new Error(`https://vpn.localhost/#invite=${secret}`);
  } catch {
    safe = diagnostic.failure();
  }
  assert.equal(safe.message, "Release journey failed at stage=client-exchange");
  assert.equal(safe.cause, undefined);
  assert.equal(safe.stack.includes(secret), false);
  assert.equal(JSON.stringify(output).includes(secret), false);
  assert.match(output.at(-1), /^E2E failed=client-exchange elapsed_ms=\d+$/);
});

test("diagnostic labels refuse arbitrary payloads without echoing them", () => {
  const output = [];
  const diagnostic = diagnostics((line) => output.push(line));
  assert.throws(() => diagnostic.checkpoint("secret-canary"), {
    message: "Unknown release stage",
  });
  assert.deepEqual(output, []);
});
