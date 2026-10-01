import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AdminView } from "../components/AdminView";
import { useAdmin } from "../state/useAdmin";
import { AdminApi, invitationLink } from "../api/admin";
import type { Job, Principal } from "../domain/types";

let role: Principal["role"];
let authenticated: boolean;
let denyMutation: boolean;
let requests: { path: string; method: string; body: Record<string, unknown> }[];
let status: Job["status"];
let cancellable: boolean;
const expiry = () => new Date(Date.now() + 3600000).toISOString();
function App() {
  return <AdminView admin={useAdmin()} />;
}
beforeEach(() => {
  role = "admin";
  authenticated = false;
  denyMutation = false;
  requests = [];
  status = "queued";
  cancellable = true;
  history.replaceState(null, "", "/");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit) => {
      const path = url.replace("/api", "").split("?")[0];
      const method = options.method ?? "GET";
      const body = options.body ? JSON.parse(String(options.body)) : {};
      requests.push({ path, method, body });
      expect(options.credentials).toBe("same-origin");
      expect(options.cache).toBe("no-store");
      const headers = options.headers as Record<string, string>;
      expect(headers["X-TTCP-Admin"]).toBe("web");
      expect(headers.Authorization).toBeUndefined();
      const response = (data: unknown, code = 200) =>
        new Response(JSON.stringify(data), { status: code });
      if (path === "/auth/login") {
        authenticated = true;
        return response({ token_type: "cookie", expires_at: expiry() });
      }
      if (!authenticated) return response({}, 401);
      if (path === "/auth/logout") {
        authenticated = false;
        return new Response(null, { status: 204 });
      }
      if (path === "/auth/me")
        return response({
          id: "actor",
          username: "operator",
          role,
          session_id: "session",
        });
      if (path === "/notifications")
        return response({
          items: [],
          total: 0,
          unread_count: 0,
          offset: 0,
          limit: 50,
        });
      if (method !== "GET" && denyMutation) return response({}, 403);
      const job = {
        id: "j",
        type: "server.preflight",
        status,
        cancellable,
        progress: 20,
        history: [
          { sequence: 1, at: expiry(), code: "queued", message: "Job queued" },
        ],
        attempts: 1,
        created_at: expiry(),
        cancel_requested_at: null,
        result: { ready: false, checks: { ssh: "pass", dns: "fail" } },
      };
      if (path === "/jobs") return response([job]);
      if (path === "/jobs/j") return response(job);
      if (path === "/jobs/j/logs")
        return new Response(
          'id: 2\nevent: log\ndata: {"sequence":2,"at":"2026-10-01T12:00:00Z","message":"Attempt started","code":"running"}\n\nevent: done\ndata: {}\n\n',
        );
      if (path === "/jobs/j/cancel") {
        status = "cancelled";
        return response({ ...job, status });
      }
      if (path === "/servers")
        return response([
          {
            id: "s",
            name: "Finland",
            hostname: "node.test",
            domain: "vpn-node.test",
            public_ip: "192.0.2.1",
            ssh_user: "manager",
            ssh_port: 22,
            enabled: true,
            status: "reachable",
            ssh_configured: true,
          },
        ]);
      if (path.endsWith("/preflight") || path.endsWith("/status"))
        return response(job, 202);
      if (path === "/vpn-users")
        return response([
          {
            id: "u",
            display_name: "Anna",
            enabled: true,
            device_limit: 1,
            access_mode: "all",
            server_ids: [],
            expires_at: null,
          },
        ]);
      if (path === "/vpn-users/u/devices")
        return response([
          { id: "d", name: "Phone", enabled: true, platform: "Android" },
        ]);
      if (path === "/vpn-users/u/devices/d/credentials")
        return response([
          { id: "c", device_id: "d", server_id: "s", status: "active" },
        ]);
      if (path === "/vpn-users/u/invitations" && method === "POST")
        return response({
          id: "i",
          token: "I".repeat(43),
          expires_at: expiry(),
        });
      if (path === "/vpn-users/u/invitations")
        return response([
          { id: "i", expires_at: expiry(), used_at: null, revoked_at: null },
        ]);
      if (method !== "GET") return response({});
      throw new Error("Unexpected request " + path);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

test("a delayed one-time invitation cannot reappear after navigation", async () => {
  authenticated = true;
  vi.stubEnv("VITE_CLIENT_PORTAL_URL", "http://vpn.localhost:8080/");
  const original = fetch;
  let finish: (response: Response) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, options: RequestInit) =>
      url.endsWith("/invitations") && options.method === "POST"
        ? new Promise<Response>((resolve) => {
            finish = resolve;
          })
        : original(url, options),
    ),
  );
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText("operator");
  await user.click(screen.getByRole("button", { name: "Invitations" }));
  await user.selectOptions(screen.getByLabelText("VPN user"), "u");
  await user.click(screen.getByRole("button", { name: "Create invitation" }));
  await user.click(screen.getByRole("button", { name: "Dashboard" }));
  finish(
    new Response(
      JSON.stringify({ id: "i", token: "I".repeat(43), expires_at: expiry() }),
    ),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled(),
  );
  await user.click(screen.getByRole("button", { name: "Invitations" }));
  expect(screen.queryByLabelText("Private invitation link")).toBeNull();
});
async function signIn() {
  const user = userEvent.setup();
  await screen.findByLabelText("Username");
  await user.type(screen.getByLabelText("Username"), "operator");
  await user.type(screen.getByLabelText("Password"), "test-only-password");
  await user.type(screen.getByLabelText("Authenticator code"), "123456");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByText("operator");
  return user;
}
test("password + TOTP → shell → preflight → live logs → confirmed cancellation", async () => {
  render(<App />);
  const user = await signIn();
  expect(
    requests.find((request) => request.path === "/auth/login")?.body,
  ).toEqual({
    username: "operator",
    password: "test-only-password",
    totp_code: "123456",
    session_mode: "cookie",
  });
  await user.click(screen.getByRole("button", { name: "Servers" }));
  await user.click(
    await screen.findByRole("button", { name: "Run pre-flight" }),
  );
  await screen.findByRole("heading", { name: "Jobs" });
  await screen.findByText("Attempt started");
  await screen.findByText("Not ready");
  await user.click(screen.getByRole("button", { name: "Cancel job" }));
  await user.click(screen.getByRole("button", { name: "Keep unchanged" }));
  expect(requests.some((request) => request.path.endsWith("/cancel"))).toBe(
    false,
  );
  await user.click(screen.getByRole("button", { name: "Cancel job" }));
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(status).toBe("cancelled"));
  expect(localStorage.length + sessionStorage.length).toBe(0);
});
test("viewer can inspect all screens and invitations, with no mutation controls", async () => {
  role = "viewer";
  authenticated = true;
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText("Viewer · read-only");
  await user.click(screen.getByRole("button", { name: "Servers" }));
  expect(screen.queryByRole("button", { name: "Run pre-flight" })).toBeNull();
  expect(screen.queryByText("Add managed server")).toBeNull();
  await user.click(screen.getByRole("button", { name: "VPN users" }));
  await user.selectOptions(screen.getByLabelText("VPN user"), "u");
  await screen.findByText(
    "Devices: 1 of 1. Credentials on other servers do not consume device quota.",
  );
  await screen.findByText("active");
  expect(screen.queryByRole("button", { name: "Add device" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Invitations" }));
  await screen.findByText("Available");
  expect(
    screen.queryByRole("button", { name: "Create invitation" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Jobs" }));
  await user.click(await screen.findByRole("button", { name: "Inspect j" }));
  await screen.findByText("Attempt started");
  expect(screen.queryByRole("button", { name: "Cancel job" })).toBeNull();
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});
test("device quota uses backend value, access is editable, invitation disappears on navigation", async () => {
  authenticated = true;
  vi.stubEnv("VITE_CLIENT_PORTAL_URL", "http://vpn.localhost:8080/");
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText("operator");
  await user.click(screen.getByRole("button", { name: "VPN users" }));
  await user.selectOptions(screen.getByLabelText("VPN user"), "u");
  await screen.findByText("active");
  expect(screen.getByRole("button", { name: "Add device" })).toBeDisabled();
  await user.selectOptions(screen.getByLabelText("Access mode"), "selected");
  await user.click(screen.getByRole("checkbox", { name: "Finland" }));
  await user.click(screen.getByRole("button", { name: "Save access" }));
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() =>
    expect(
      requests.some(
        (request) =>
          request.path.endsWith("/access") &&
          request.body.server_ids?.toString() === "s",
      ),
    ).toBe(true),
  );
  await user.click(screen.getByRole("button", { name: "Invitations" }));
  await user.click(screen.getByRole("button", { name: "Create invitation" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Private invitation link")).toHaveValue(
      "http://vpn.localhost:8080/#invite=" + "I".repeat(43),
    ),
  );
  await user.click(screen.getByRole("button", { name: "Dashboard" }));
  expect(screen.queryByLabelText("Private invitation link")).toBeNull();
  expect(localStorage.length + sessionStorage.length).toBe(0);
  vi.unstubAllEnvs();
});
test("unsafe running jobs have no cancel action and server-side denial is actionable", async () => {
  authenticated = true;
  status = "running";
  cancellable = false;
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText("operator");
  await user.click(screen.getByRole("button", { name: "Jobs" }));
  await user.click(await screen.findByRole("button", { name: "Inspect j" }));
  await screen.findByText("Attempt started");
  expect(screen.queryByRole("button", { name: "Cancel job" })).toBeNull();
  denyMutation = true;
  await user.click(screen.getByRole("button", { name: "Servers" }));
  await user.click(
    await screen.findByRole("button", { name: "Run pre-flight" }),
  );
  await screen.findByText(
    "This action is not permitted. Refresh to check your current role.",
  );
});
test("refresh restores cookie session; logout and expiry clear protected content", async () => {
  authenticated = true;
  const first = render(<App />);
  await screen.findByText("operator");
  first.unmount();
  render(<App />);
  await screen.findByText("operator");
  expect(requests.some((request) => request.path === "/auth/login")).toBe(
    false,
  );
  authenticated = false;
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByRole("heading", { name: "Sign in" });
  expect(screen.queryByText("operator")).toBeNull();
  await signIn();
  await user.click(screen.getByRole("button", { name: "Sign out" }));
  await screen.findByRole("heading", { name: "Sign in" });
  expect(authenticated).toBe(false);
});
test("API ignores stale results after session clearing and invitation sharing requires separate origin", async () => {
  const api = new AdminApi();
  let finish: (response: Response) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const pending = api.me();
  api.clear();
  finish(new Response(JSON.stringify({ role: "admin" })));
  await expect(pending).rejects.toMatchObject({ status: 401 });
  vi.stubEnv("VITE_CLIENT_PORTAL_URL", window.location.origin);
  expect(() => invitationLink("I".repeat(43))).toThrow("separate origin");
  vi.unstubAllEnvs();
});
