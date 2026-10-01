import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AdminView } from "../components/AdminView";
import { useAdmin } from "../state/useAdmin";
import { useVisibility } from "../state/useVisibility";
import { AdminApi } from "../api/admin";
import type { Monitoring, Notification, Principal } from "../domain/types";

const at = "2026-10-01T12:00:00Z";
let role: Principal["role"];
let authenticated: boolean;
let fail: string;
let denyRead: boolean;
let entries: Notification[];
let requests: {
  path: string;
  method: string;
  query: URLSearchParams;
  body: Record<string, unknown>;
}[];
const metrics = {
  cpu_percent: 12.5,
  memory_percent: 42,
  disk_percent: 60,
  load1: 0.7,
  network_receive_bytes_per_second: 2048,
  network_transmit_bytes_per_second: 1024,
};
let monitoring: Monitoring;
function App() {
  return <AdminView admin={useAdmin()} />;
}
const reply = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
beforeEach(() => {
  role = "admin";
  authenticated = true;
  fail = "";
  denyRead = false;
  requests = [];
  entries = [
    {
      id: "event",
      created_at: at,
      severity: "critical",
      title: "Job failed",
      message:
        "An operational job failed. Inspect its safe job log for details.",
      target_type: "job",
      target_id: "job",
      request_id: "safe-request",
      read_at: null,
    },
  ];
  monitoring = {
    generated_at: at,
    window: "1h",
    step_seconds: 60,
    system: {
      status: "healthy",
      postgres: "healthy",
      redis: "healthy",
      victoriametrics: "healthy",
      collector: "healthy",
    },
    servers: [
      {
        id: "node",
        name: "Finland",
        enabled: true,
        health: "healthy",
        management_status: "reachable",
        last_seen_at: at,
        observed_at: at,
        metrics,
        service: {
          active: true,
          process_running: true,
          probe: "healthy",
          automatic_restarts: 1,
        },
        recent: [{ at, ...metrics }],
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit) => {
      const parsed = new URL(url, "http://admin.localhost");
      const path = parsed.pathname.replace("/api", "");
      const method = options.method ?? "GET";
      const body = options.body ? JSON.parse(String(options.body)) : {};
      requests.push({ path, method, query: parsed.searchParams, body });
      expect(options.credentials).toBe("same-origin");
      expect(options.cache).toBe("no-store");
      expect((options.headers as Record<string, string>)["X-TTCP-Admin"]).toBe(
        "web",
      );
      if (!authenticated) return reply({}, 401);
      if (path === fail) return reply({}, 503);
      if (path === "/auth/me")
        return reply({
          id: "actor",
          session_id: "session",
          username: "operator",
          role,
        });
      if (path === "/auth/logout") {
        authenticated = false;
        return new Response(null, { status: 204 });
      }
      if (["/servers", "/vpn-users", "/jobs"].includes(path)) return reply([]);
      if (path === "/jobs/job")
        return reply({
          id: "job",
          type: "server.preflight",
          target_type: "server",
          target_id: "node",
          status: "failed",
          progress: 0,
          attempts: 1,
          created_at: at,
          error_message: "Safe failure detail",
          cancellable: false,
          cancel_requested_at: null,
          history: [],
          result: {},
        });
      if (path === "/jobs/job/logs")
        return new Response("event: done\ndata: {}\n\n");
      if (path === "/monitoring")
        return reply({
          ...monitoring,
          window: parsed.searchParams.get("window"),
        });
      if (path === "/audit") {
        const offset = Number(parsed.searchParams.get("offset"));
        const items =
          parsed.searchParams.get("action") === "none"
            ? []
            : [
                {
                  id: `audit-${offset}`,
                  created_at: at,
                  actor_type: "admin",
                  actor_id: "actor",
                  action: offset ? "auth.login" : "server.create",
                  target_type: "server",
                  target_id: "node",
                  request_id: "safe-request",
                  result: "success",
                },
              ];
        return reply({
          items,
          total: items.length ? 51 : 0,
          offset,
          limit: 50,
        });
      }
      if (path === "/notifications") {
        const items =
          parsed.searchParams.get("unread_only") === "true"
            ? entries.filter((entry) => !entry.read_at)
            : entries;
        return reply({
          items,
          total: items.length,
          offset: 0,
          limit: 50,
          unread_count: entries.filter((entry) => !entry.read_at).length,
        });
      }
      if (path === "/notifications/event/read") {
        if (denyRead) return reply({}, 403);
        entries = entries.map((entry) => ({
          ...entry,
          read_at: body.read ? at : null,
        }));
        return reply(entries[0]);
      }
      throw new Error("Unexpected request " + path);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const notificationsButton = () =>
  screen.getByRole("button", { name: /^Notifications/ });

test("audit filters are encoded, paginate on the backend and reset to newest page", async () => {
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText("operator");
  await user.click(screen.getByRole("button", { name: "Audit" }));
  await screen.findByText("server.create");
  await user.click(screen.getByRole("button", { name: "Older audit events" }));
  await screen.findByText("auth.login");
  expect(
    requests
      .filter((request) => request.path === "/audit")
      .at(-1)
      ?.query.get("offset"),
  ).toBe("50");
  await user.type(screen.getByLabelText("Action"), "none");
  await user.type(screen.getByLabelText("Actor ID"), "actor & safe");
  await user.selectOptions(screen.getByLabelText("Actor type"), "admin");
  fireEvent.change(screen.getByLabelText("From (local time)"), {
    target: { value: "2026-10-01T08:15" },
  });
  fireEvent.change(screen.getByLabelText("Until (local time)"), {
    target: { value: "2026-10-01T09:45" },
  });
  await user.click(screen.getByRole("button", { name: "Apply filters" }));
  await screen.findByText("No audit events match these filters.");
  const request = requests
    .filter((request) => request.path === "/audit")
    .at(-1)!;
  expect(request.query.get("offset")).toBe("0");
  expect(request.query.get("actor_id")).toBe("actor & safe");
  expect(request.query.get("since")).toBe(
    new Date("2026-10-01T08:15").toISOString(),
  );
  await user.click(screen.getByRole("button", { name: "Dashboard" }));
  await user.click(screen.getByRole("button", { name: "Audit" }));
  await screen.findByText("No audit events match these filters.");
  expect(screen.getByLabelText("Action")).toHaveValue("none");
  expect(screen.getByLabelText("Actor ID")).toHaveValue("actor & safe");
  expect(screen.getByLabelText("Actor type")).toHaveValue("admin");
  expect(screen.getByLabelText("From (local time)")).toHaveValue(
    "2026-10-01T08:15",
  );
  expect(screen.getByLabelText("Until (local time)")).toHaveValue(
    "2026-10-01T09:45",
  );
  expect(
    screen.getByRole("button", { name: "Older audit events" }),
  ).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Clear filters" }));
  await screen.findByText("server.create");
  expect(screen.getByLabelText("Action")).toHaveValue("");
  expect(screen.getByLabelText("Actor type")).toHaveValue("");
  expect(screen.getByLabelText("From (local time)")).toHaveValue("");
});

test("sparse trend samples remain visible as points without connecting missing data", async () => {
  monitoring.servers[0].recent = [
    { at, ...metrics },
    {
      at: "2026-10-01T12:01:00Z",
      ...metrics,
      cpu_percent: null,
      memory_percent: null,
    },
    {
      at: "2026-10-01T12:02:00Z",
      ...metrics,
      cpu_percent: null,
      memory_percent: null,
    },
    {
      at: "2026-10-01T12:03:00Z",
      ...metrics,
      cpu_percent: 20,
      memory_percent: 44,
    },
  ];
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText("operator");
  await user.click(screen.getByRole("button", { name: "Monitoring" }));
  const trend = await screen.findByRole("img", {
    name: /Recent memory usage trend/,
  });
  expect(trend.querySelectorAll("circle")).toHaveLength(2);
  expect(trend.querySelector("polyline")).toBeNull();
  expect(screen.getByText("Latest sampled: 44.0%")).toBeInTheDocument();
});

test("failed monitoring polling labels retained observations as stale", async () => {
  vi.useFakeTimers();
  render(<App />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Monitoring" }));
  await act(async () => {});
  expect(screen.getByText("Overall health: healthy")).toBeInTheDocument();
  fail = "/monitoring";
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(
    screen.getByText(
      /Monitoring could not be refreshed\. Showing the last successful observation/,
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText("The service is unavailable. Please retry."),
  ).toBeInTheDocument();
});

test("notification read/unread lifecycle updates badge and unread filtering without optimistic success", async () => {
  render(<App />);
  const user = userEvent.setup();
  await screen.findByLabelText("1 unread");
  await user.click(notificationsButton());
  await screen.findByText("Job failed");
  denyRead = true;
  await user.click(screen.getByRole("button", { name: "Mark read" }));
  await screen.findByText(
    "This action is not permitted. Refresh to check your current role.",
  );
  expect(screen.getByText("Unread", { exact: true })).toBeInTheDocument();
  denyRead = false;
  await user.click(screen.getByRole("button", { name: "Mark read" }));
  await screen.findByRole("button", { name: "Mark unread" });
  expect(screen.queryByLabelText("1 unread")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Mark unread" }));
  await screen.findByLabelText("1 unread");
  await user.click(screen.getByRole("checkbox", { name: "Unread only" }));
  await screen.findByText("Job failed");
  await user.click(screen.getByRole("button", { name: "Mark read" }));
  await screen.findByText("No unread notifications.");
  expect(
    requests.some(
      (request) =>
        request.path === "/notifications/event/read" &&
        request.method === "PUT" &&
        request.body.read === true,
    ),
  ).toBe(true);
  expect(localStorage.length + sessionStorage.length).toBe(0);
});

test("viewer inspects monitoring, audit and notifications with no read-state mutation controls", async () => {
  role = "viewer";
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText("Viewer · read-only");
  await user.click(screen.getByRole("button", { name: "Monitoring" }));
  await screen.findByText("Overall health: healthy");
  expect(screen.getByText("12.5%", { exact: true })).toBeInTheDocument();
  expect(
    screen.getByRole("img", { name: /Recent CPU usage trend/ }),
  ).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Recent period"), "24h");
  await waitFor(() =>
    expect(
      requests
        .filter((request) => request.path === "/monitoring")
        .at(-1)
        ?.query.get("window"),
    ).toBe("24h"),
  );
  await user.click(screen.getByRole("button", { name: "Audit" }));
  await screen.findByText("server.create");
  await user.click(notificationsButton());
  await screen.findByText("Job failed");
  expect(screen.queryByRole("button", { name: "Mark read" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Mark unread" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Inspect job" }));
  await screen.findByText("Safe failure detail");
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});

test("missing monitoring data and dependency errors remain visible and can recover", async () => {
  monitoring.system = {
    status: "degraded",
    postgres: "healthy",
    redis: "unavailable",
    victoriametrics: "unavailable",
    collector: "unknown",
  };
  monitoring.servers[0].metrics = {
    cpu_percent: null,
    memory_percent: null,
    disk_percent: null,
    load1: null,
    network_receive_bytes_per_second: null,
    network_transmit_bytes_per_second: null,
  };
  monitoring.servers[0].health = "unknown";
  monitoring.servers[0].service.active = null;
  monitoring.servers[0].service.process_running = null;
  monitoring.servers[0].recent = [];
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText("operator");
  await user.click(screen.getByRole("button", { name: "Monitoring" }));
  await screen.findByText("Overall health: degraded");
  expect(screen.getAllByText("Unavailable", { exact: true })).toHaveLength(6);
  expect(screen.getAllByText("No recent samples.")).toHaveLength(2);
  expect(screen.queryByText("0.0%")).toBeNull();
  fail = "/monitoring";
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText("The service is unavailable. Please retry.");
  fail = "";
  monitoring.servers = [];
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText("No managed servers to monitor.");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("delayed audit responses cannot overwrite newer filters or reappear after logout", async () => {
  const original = fetch;
  const pending: ((response: Response) => void)[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, options: RequestInit) =>
      url.startsWith("/api/audit")
        ? new Promise<Response>((resolve) => pending.push(resolve))
        : original(url, options),
    ),
  );
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText("operator");
  await user.click(screen.getByRole("button", { name: "Audit" }));
  await screen.findByText("Loading audit events…");
  await user.click(screen.getByRole("button", { name: "Clear filters" }));
  await waitFor(() => expect(pending.length).toBe(2));
  pending[1](reply({ items: [], total: 0, offset: 0, limit: 50 }));
  await screen.findByText("No audit events match these filters.");
  await act(async () => {
    pending[0](
      reply({
        items: [
          { id: "stale", action: "stale-secret-free-event", created_at: at },
        ],
        total: 1,
        offset: 0,
        limit: 50,
      }),
    );
  });
  expect(screen.queryByText("stale-secret-free-event")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Clear filters" }));
  await waitFor(() => expect(pending.length).toBe(3));
  await user.click(screen.getByRole("button", { name: "Sign out" }));
  await screen.findByRole("heading", { name: "Sign in" });
  pending[2](
    reply({
      items: [
        { id: "stale", action: "stale-secret-free-event", created_at: at },
      ],
      total: 1,
      offset: 0,
      limit: 50,
    }),
  );
  await waitFor(() =>
    expect(screen.queryByText("stale-secret-free-event")).toBeNull(),
  );
});

test("visibility polling refreshes recent data and stops with session or component lifetime", async () => {
  vi.useFakeTimers();
  const api = new AdminApi();
  const principal: Principal = {
    id: "actor",
    session_id: "session",
    username: "operator",
    role: "admin",
  };
  const { result, rerender, unmount } = renderHook(
    ({ person }: { person: Principal | null }) =>
      useVisibility(api, person, "Monitoring", 0),
    { initialProps: { person: principal as Principal | null } },
  );
  await act(async () => {});
  expect(result.current.monitoring?.system.status).toBe("healthy");
  expect(result.current.notifications?.unread_count).toBe(1);
  monitoring.system.status = "degraded";
  entries = [];
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(result.current.monitoring?.system.status).toBe("degraded");
  expect(result.current.notifications?.unread_count).toBe(0);
  rerender({ person: null });
  expect(result.current.monitoring).toBeNull();
  expect(result.current.notifications).toBeNull();
  const count = requests.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(requests).toHaveLength(count);
  unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(requests).toHaveLength(count);
});

test("notification and audit HTTP errors show retryable errors and expired sessions clear events", async () => {
  fail = "/notifications";
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText("operator");
  await user.click(notificationsButton());
  await screen.findByText("The service is unavailable. Please retry.");
  fail = "/audit";
  await user.click(screen.getByRole("button", { name: "Audit" }));
  await screen.findByText("The service is unavailable. Please retry.");
  fail = "";
  await user.click(notificationsButton());
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText("Job failed");
  authenticated = false;
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByRole("heading", { name: "Sign in" });
  expect(screen.queryByText("Job failed")).toBeNull();
});
