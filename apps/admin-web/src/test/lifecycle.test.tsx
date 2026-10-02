import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ServerLifecycle } from "../components/ServerLifecycle";
import { AdminApi } from "../api/admin";
import type { Server } from "../domain/types";
import type { AdminState } from "../state/useAdmin";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function setup(role = "admin", changes = {}) {
  const node = {
    id: "s",
    name: "Finland",
    enabled: true,
    acme_http: true,
    lifecycle_state: "unknown",
    trusttunnel_version: null,
    desired_trusttunnel_version: null,
    preflight_passed_at: new Date().toISOString(),
    lifecycle_job_id: "j",
    ...changes,
  } as Server;
  const lifecycle = vi.fn();
  const selectJob = vi.fn();
  const admin = {
    principal: { role },
    busy: false,
    actions: { lifecycle },
    selectJob,
    navigate: vi.fn(),
  } as unknown as AdminState;
  render(<ServerLifecycle node={node} admin={admin} />);
  return { lifecycle, selectJob, admin };
}

test("deploy gates on preflight and version; submitted release and contact reach the action", async () => {
  const user = userEvent.setup();
  const { lifecycle } = setup();
  expect(screen.getByRole("button", { name: "Deploy" })).toBeDisabled();
  await user.type(
    screen.getByLabelText("Release version for Finland"),
    "1.2.3",
  );
  await user.type(
    screen.getByLabelText("Certificate email for Finland"),
    "operator@example.org",
  );
  await user.click(screen.getByRole("button", { name: "Deploy" }));
  expect(lifecycle).toHaveBeenCalledWith(
    "s",
    "deploy",
    "1.2.3",
    "operator@example.org",
  );
  cleanup();
  setup("admin", { preflight_passed_at: null });
  expect(screen.getByRole("button", { name: "Deploy" })).toBeDisabled();
});

test("installed workload supports confirmed update, restart, uninstall and job navigation", async () => {
  const user = userEvent.setup();
  const { lifecycle, selectJob } = setup("admin", {
    lifecycle_state: "installed",
    trusttunnel_version: "1.2.3",
    desired_trusttunnel_version: "1.2.4",
  });
  expect(screen.getByText(/Installed version: 1.2.3/)).toBeInTheDocument();
  await user.type(
    screen.getByLabelText("Release version for Finland"),
    "1.2.4",
  );
  await user.click(screen.getByRole("button", { name: "Update TrustTunnel" }));
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  expect(lifecycle).toHaveBeenCalledWith("s", "update", "1.2.4");
  await user.click(screen.getByRole("button", { name: "Restart TrustTunnel" }));
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  expect(lifecycle).toHaveBeenCalledWith("s", "restart");
  await user.click(
    screen.getByRole("button", { name: "Uninstall TrustTunnel" }),
  );
  expect(
    screen.getByText(/Management SSH access is preserved/),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  expect(lifecycle).toHaveBeenCalledWith("s", "uninstall");
  await user.click(screen.getByRole("button", { name: "View lifecycle job" }));
  expect(selectJob).toHaveBeenCalledWith("j");
});

test("viewer sees failed state and job link with no privileged lifecycle controls", () => {
  setup("viewer", { lifecycle_state: "failed" });
  expect(screen.getByText("failed")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Uninstall TrustTunnel" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByLabelText("Release version for Finland"),
  ).not.toBeInTheDocument();
});

test("API sends only lifecycle schema and fresh idempotency key", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ id: "j" }), { status: 202 }),
    );
  vi.stubGlobal("fetch", fetch);
  await new AdminApi().lifecycle("s", "update", "1.2.4");
  expect(fetch.mock.calls[0][0]).toBe("/api/servers/s/update");
  const body = JSON.parse(fetch.mock.calls[0][1].body);
  expect(body.version).toBe("1.2.4");
  expect(body.idempotency_key).toBeTypeOf("string");
  expect(Object.keys(body).sort()).toEqual(["idempotency_key", "version"]);
});
