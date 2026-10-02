import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AdminView } from "../components/AdminView";
import { useAdmin } from "../state/useAdmin";
import type { BackupHistory, Principal } from "../domain/types";

let role: Principal["role"];
let history: BackupHistory;
let fail: number;
let requests: { method: string; path: string; body: Record<string, string> }[];
const at = "2026-10-02T12:00:00Z";
function App() {
  return <AdminView admin={useAdmin()} />;
}
const reply = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });

beforeEach(() => {
  role = "admin";
  fail = 0;
  requests = [];
  history = {
    enabled: true,
    items: [],
    total: 0,
    offset: 0,
    limit: 50,
    latest: null,
    last_success: null,
    age_seconds: null,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit) => {
      const parsed = new URL(url, "https://admin.localhost");
      const path = parsed.pathname.replace("/api", "");
      const method = options.method ?? "GET";
      const body = options.body ? JSON.parse(String(options.body)) : {};
      requests.push({ method, path, body });
      if (path === "/auth/me")
        return reply({
          id: "operator",
          session_id: "session",
          username: "operator",
          role,
        });
      if (path === "/backups" && fail === 503) return reply({}, 503);
      if (path === "/backups") return reply(history);
      if (path === "/backups/run") {
        if (fail) return reply({}, fail);
        const backup = {
          id: "backup",
          status: "queued" as const,
          job_status: "queued",
          created_at: at,
          started_at: null,
          finished_at: null,
          attempts: 0,
          max_attempts: 3,
          error_code: null,
        };
        history = { ...history, items: [backup], latest: backup, total: 1 };
        return reply({ ...backup, type: "backup.run" }, 202);
      }
      if (path === "/notifications")
        return reply({
          items: [],
          unread_count: 0,
          total: 0,
          offset: 0,
          limit: 50,
        });
      return reply([]);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
async function open() {
  const user = userEvent.setup();
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Backups" }));
  await screen.findByText("No backups yet.");
  return user;
}

test("admin queues fixed action and sees actual asynchronous state", async () => {
  const user = await open();
  expect(
    screen.queryByRole("button", { name: /restore/i }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Run backup now" }));
  await screen.findByText("Backup queued. History will update as it runs.");
  expect(
    await screen.findByRole("button", { name: "Run backup now" }),
  ).toBeDisabled();
  expect(screen.getAllByText("queued").length).toBeGreaterThan(0);
  const request = requests.find((r) => r.path === "/backups/run")!;
  expect(Object.keys(request.body)).toEqual(["idempotency_key"]);
  expect(request.body.idempotency_key).toMatch(/^[0-9a-f-]{36}$/);
});

test("viewer reads backup history without mutation controls", async () => {
  role = "viewer";
  await open();
  expect(screen.getByText("Never run")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Run backup now" }),
  ).not.toBeInTheDocument();
  expect(requests.filter((r) => r.method !== "GET")).toHaveLength(0);
});

test("failed requests reuse the same idempotency key and handle role denial", async () => {
  const user = await open();
  fail = 403;
  await user.click(screen.getByRole("button", { name: "Run backup now" }));
  await screen.findByText(/This action is not permitted/);
  fail = 0;
  await user.click(screen.getByRole("button", { name: "Run backup now" }));
  await screen.findByText("Backup queued. History will update as it runs.");
  const calls = requests.filter((r) => r.path === "/backups/run");
  expect(calls).toHaveLength(2);
  expect(calls[0].body).toEqual(calls[1].body);
});

test("unknown outcome, verified snapshot age, and history pagination", async () => {
  const user = await open();
  const backup = {
    id: "backup",
    status: "unknown" as const,
    job_status: "failed",
    created_at: at,
    started_at: at,
    finished_at: at,
    attempts: 3,
    max_attempts: 3,
    error_code: "backup_unknown",
  };
  history = {
    ...history,
    items: [backup],
    latest: backup,
    last_success: { ...backup, status: "succeeded" },
    age_seconds: 7200,
    total: 51,
  };
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText(/An operator must verify/);
  expect(screen.getByText("Snapshot age: 120 minutes")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Next backups" }));
  await waitFor(() =>
    expect(
      requests.filter((r) => r.path === "/backups").length,
    ).toBeGreaterThan(2),
  );
  expect(
    screen.queryByRole("button", { name: /restore/i }),
  ).not.toBeInTheDocument();
});

test("disabled configuration and unavailable history remain explicit", async () => {
  const user = await open();
  history.enabled = false;
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText(/Remote backups are not configured/);
  expect(screen.getByRole("button", { name: "Run backup now" })).toBeDisabled();
  fail = 503;
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText("The service is unavailable. Please retry.");
});
