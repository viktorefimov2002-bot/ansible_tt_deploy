import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { AdminApi } from "../api/admin";
import { ServerConfiguration } from "../components/ServerConfiguration";
import { defaultConfiguration } from "../domain/configuration";
import type { ConfigRevision, Principal, Server } from "../domain/types";
import type { AdminState } from "../state/useAdmin";
import { useServerConfiguration } from "../state/useServerConfiguration";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const revision: ConfigRevision = {
  id: "r",
  server_id: "s",
  revision: 1,
  config: { ...defaultConfiguration },
  created_by: "a",
  created_at: "2026-10-02T09:00:00Z",
  applied_at: null,
  status: "validated",
  job_id: null,
  failure_code: null,
  failure_message: null,
};
function setup(role = "admin", change = {}, nodeChange = {}) {
  const actions = {
    createConfigRevision: vi.fn(),
    applyConfigRevision: vi.fn(),
  };
  const node = {
    id: "s",
    name: "Finland",
    enabled: true,
    trusttunnel_version: "1.2.3",
    lifecycle_state: "installed",
    config_state: "idle",
    config_revision_id: null,
    config_job_id: null,
    ...nodeChange,
  } as Server;
  const admin = {
    principal: { role },
    busy: false,
    configServerId: "s",
    configLoading: false,
    configError: "",
    configOffset: 0,
    configRevisions: [revision],
    actions,
    navigate: vi.fn(),
    selectJob: vi.fn(),
    selectConfigServer: vi.fn(),
    pageConfig: vi.fn(),
    ...change,
  } as unknown as AdminState;
  render(<ServerConfiguration node={node} admin={admin} />);
  return { admin, actions };
}

test("creates structured revisions separately from confirmed apply and reuses retry keys", async () => {
  const user = userEvent.setup();
  const { actions } = setup();
  await user.click(screen.getByRole("button", { name: "Create revision" }));
  await user.click(screen.getByRole("button", { name: "Create revision" }));
  expect(actions.createConfigRevision).toHaveBeenCalledWith(
    "s",
    defaultConfiguration,
    expect.any(String),
  );
  expect(actions.createConfigRevision.mock.calls[0][2]).toBe(
    actions.createConfigRevision.mock.calls[1][2],
  );
  expect(actions.applyConfigRevision).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("Allow IPv6 destinations"));
  await user.click(screen.getByRole("button", { name: "Create revision" }));
  expect(actions.createConfigRevision.mock.calls[2][1].ipv6_available).toBe(
    false,
  );
  expect(actions.createConfigRevision.mock.calls[2][2]).not.toBe(
    actions.createConfigRevision.mock.calls[0][2],
  );
  await user.click(screen.getByRole("button", { name: "Apply revision 1" }));
  expect(screen.getByText(/restored automatically/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Keep unchanged" }));
  expect(actions.applyConfigRevision).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Apply revision 1" }));
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  expect(actions.applyConfigRevision).toHaveBeenCalledWith(
    "s",
    "r",
    expect.any(String),
  );
});

test("viewer reads current history, failure and job without mutation controls", async () => {
  const user = userEvent.setup();
  const { admin } = setup(
    "viewer",
    {
      configRevisions: [
        {
          ...revision,
          status: "rolled_back",
          job_id: "job",
          failure_message: "Apply failed; previous configuration restored.",
        },
      ],
    },
    { config_revision_id: "r", config_state: "rolled_back" },
  );
  expect(screen.getByText("Revision 1 · Current")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "previous configuration restored",
  );
  expect(
    screen.queryByRole("button", { name: "Create revision" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Apply revision 1" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "View revision 1 job" }));
  expect(admin.navigate).toHaveBeenCalledWith("Jobs");
  expect(admin.selectJob).toHaveBeenCalledWith("job");
});

test("shows loading, empty, unavailable and pending state; unsafe apply stays disabled", () => {
  setup(
    "admin",
    { configLoading: true, configRevisions: [] },
    { config_state: "apply_pending" },
  );
  expect(screen.getByRole("status")).toHaveTextContent("Loading revisions");
  expect(
    screen.getByRole("button", { name: "Create revision" }),
  ).toBeDisabled();
  cleanup();
  setup("admin", {
    configRevisions: [],
    configError: "Configuration history unavailable",
  });
  expect(screen.getByRole("alert")).toHaveTextContent("unavailable");
  cleanup();
  setup("admin", { configRevisions: [] });
  expect(screen.getByText(/No revisions on this page/)).toBeInTheDocument();
  cleanup();
  setup("admin", {}, { trusttunnel_version: null });
  expect(
    screen.getByRole("button", { name: "Apply revision 1" }),
  ).toBeDisabled();
});

test("configuration API sends closed payloads and caller retry keys", async () => {
  const fetch = vi
    .fn()
    .mockImplementation(
      async () => new Response(JSON.stringify({ id: "j" }), { status: 202 }),
    );
  vi.stubGlobal("fetch", fetch);
  const api = new AdminApi();
  await api.createConfigRevision("s", defaultConfiguration, "create-key");
  expect(fetch.mock.calls[0][0]).toBe("/api/servers/s/config-revisions");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
    config: defaultConfiguration,
    idempotency_key: "create-key",
  });
  await api.applyConfigRevision("s", "r", "apply-key");
  expect(fetch.mock.calls[1][0]).toBe(
    "/api/servers/s/config-revisions/r/apply",
  );
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
    idempotency_key: "apply-key",
  });
  expect(fetch.mock.calls[1][1].credentials).toBe("same-origin");
});

test("invalid legacy revision has metadata only and cannot be copied or applied", () => {
  setup("admin", {
    configRevisions: [{ ...revision, config: null, validation_valid: false }],
  });
  expect(
    screen.getByText(/historical revision has invalid settings/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Apply revision 1" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Use revision 1 settings" }),
  ).not.toBeInTheDocument();
});

test("history state discards late server selection responses and clears at session reset", async () => {
  const api = new AdminApi();
  let resolveFirst: (rows: ConfigRevision[]) => void = () => {};
  vi.spyOn(api, "configRevisions")
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    )
    .mockResolvedValueOnce([
      { ...revision, id: "r2", server_id: "s2", revision: 2 },
    ]);
  function Harness() {
    const state = useServerConfiguration(api, { id: "a" } as Principal, 0);
    return (
      <>
        <button onClick={() => state.selectConfigServer("s")}>
          First server
        </button>
        <button onClick={() => state.selectConfigServer("s2")}>
          Second server
        </button>
        <button onClick={state.resetConfig}>Reset session</button>
        {state.configRevisions.map((row) => (
          <p key={row.id}>History {row.revision}</p>
        ))}
      </>
    );
  }
  const user = userEvent.setup();
  render(<Harness />);
  await user.click(screen.getByRole("button", { name: "First server" }));
  await user.click(screen.getByRole("button", { name: "Second server" }));
  expect(await screen.findByText("History 2")).toBeInTheDocument();
  resolveFirst([revision]);
  await waitFor(() =>
    expect(screen.queryByText("History 1")).not.toBeInTheDocument(),
  );
  await user.click(screen.getByRole("button", { name: "Reset session" }));
  expect(screen.queryByText("History 2")).not.toBeInTheDocument();
});
