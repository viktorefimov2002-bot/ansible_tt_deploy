import type {
  AuditEvent,
  AuditFilters,
  Credential,
  Device,
  Invitation,
  Job,
  LogEvent,
  ListPage,
  Monitoring,
  MonitoringWindow,
  Notification,
  NotificationPage,
  Principal,
  Server,
  ServerInput,
  UserInput,
  VpnUser,
} from "../domain/types";

export class ApiError extends Error {
  constructor(public status: number) {
    super(
      status === 401
        ? "Your session ended. Sign in again."
        : status === 403
          ? "This action is not permitted. Refresh to check your current role."
          : status === 409
            ? "The state changed or this operation is unavailable. Refresh and try again."
            : status === 422
              ? "Check the supplied values."
              : status === 429
                ? "Too many attempts. Wait before trying again."
                : status === 404
                  ? "This item is no longer available."
                  : "The service is unavailable. Please retry.",
    );
  }
}
export class AdminApi {
  private generation = 0;
  onUnauthorized = () => {};
  clear() {
    this.generation++;
  }
  async request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    const generation = this.generation;
    const response = await fetch("/api" + path, {
      method,
      cache: "no-store",
      credentials: "same-origin",
      referrerPolicy: "no-referrer",
      signal: AbortSignal.timeout(60000),
      headers: { "Content-Type": "application/json", "X-TTCP-Admin": "web" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (generation !== this.generation) throw new ApiError(401);
    if (!response.ok) {
      if (response.status === 401) {
        this.clear();
        this.onUnauthorized();
      }
      throw new ApiError(response.status);
    }
    const data =
      response.status === 204 ? (undefined as T) : await response.json();
    if (generation !== this.generation) throw new ApiError(401);
    return data;
  }
  login = (username: string, password: string, totp_code: string) =>
    this.request("/auth/login", "POST", {
      username,
      password,
      totp_code,
      session_mode: "cookie",
    });
  me = () => this.request<Principal>("/auth/me");
  async logout() {
    try {
      await this.request("/auth/logout", "POST");
    } finally {
      this.clear();
    }
  }
  async servers() {
    const rows: Server[] = [];
    for (let offset = 0; ; offset += 100) {
      const page = await this.request<Server[]>(
        `/servers?offset=${offset}&limit=100`,
      );
      rows.push(...page);
      if (page.length < 100) return rows;
    }
  }
  createServer = (
    body: ServerInput & {
      credentials: { host_key: string; private_key: string };
    },
  ) => this.request<Server>("/servers", "POST", body);
  updateServer = (id: string, body: ServerInput) =>
    this.request(`/servers/${id}`, "PUT", body);
  serverEnabled = (id: string, enabled: boolean) =>
    this.request(`/servers/${id}/enabled`, "PUT", { enabled });
  rotateSSH = (id: string, host_key: string, private_key: string) =>
    this.request(`/servers/${id}/credentials`, "PUT", {
      host_key,
      private_key,
    });
  diagnostic = (id: string, kind: "preflight" | "status") =>
    this.request<Job>(`/servers/${id}/${kind}`, "POST", {
      idempotency_key: crypto.randomUUID(),
    });
  users = () => this.request<VpnUser[]>("/vpn-users");
  createUser = (body: UserInput) =>
    this.request<VpnUser>("/vpn-users", "POST", body);
  updateUser = (id: string, body: UserInput) =>
    this.request(`/vpn-users/${id}`, "PATCH", body);
  userEnabled = (id: string, enabled: boolean) =>
    this.request(`/vpn-users/${id}/enabled`, "PUT", { enabled });
  access = (
    id: string,
    access_mode: "selected" | "all",
    server_ids: string[],
  ) =>
    this.request(`/vpn-users/${id}/access`, "PUT", { access_mode, server_ids });
  devices = (user: string) =>
    this.request<Device[]>(`/vpn-users/${user}/devices`);
  createDevice = (user: string, name: string, platform: string) =>
    this.request(`/vpn-users/${user}/devices`, "POST", {
      name,
      platform: platform || null,
    });
  revokeDevice = (user: string, device: string) =>
    this.request(`/vpn-users/${user}/devices/${device}`, "DELETE");
  credentials = (user: string, device: string) =>
    this.request<Credential[]>(
      `/vpn-users/${user}/devices/${device}/credentials`,
    );
  credential = (user: string, device: string, server: string, revoke = false) =>
    this.request(
      `/vpn-users/${user}/devices/${device}/credentials/${server}`,
      revoke ? "DELETE" : "POST",
    );
  invitations = (user: string) =>
    this.request<Invitation[]>(`/vpn-users/${user}/invitations`);
  invite = (user: string, lifetime_seconds: number) =>
    this.request<Invitation & { token: string }>(
      `/vpn-users/${user}/invitations`,
      "POST",
      { lifetime_seconds },
    );
  revokeInvite = (user: string, id: string) =>
    this.request(`/vpn-users/${user}/invitations/${id}`, "DELETE");
  jobs = (offset = 0) => this.request<Job[]>(`/jobs?offset=${offset}&limit=50`);
  job = (id: string) => this.request<Job>(`/jobs/${id}`);
  cancel = (id: string) => this.request(`/jobs/${id}/cancel`, "POST");
  audit = (offset: number, filters: AuditFilters) => {
    const query = new URLSearchParams({ offset: String(offset), limit: "50" });
    Object.entries(filters).forEach(([name, value]) => {
      if (value) query.set(name, value);
    });
    return this.request<ListPage<AuditEvent>>(`/audit?${query}`);
  };
  notifications = (offset = 0, unreadOnly = false) =>
    this.request<NotificationPage>(
      `/notifications?offset=${offset}&limit=50&unread_only=${unreadOnly}`,
    );
  notificationRead = (id: string, read: boolean) =>
    this.request<Notification>(`/notifications/${id}/read`, "PUT", { read });
  monitoring = (window: MonitoringWindow) =>
    this.request<Monitoring>(`/monitoring?window=${window}`);
  async logs(
    id: string,
    signal: AbortSignal,
    onLog: (log: LogEvent) => void,
    after: number,
  ) {
    const generation = this.generation;
    const response = await fetch(`/api/jobs/${id}/logs`, {
      signal,
      credentials: "same-origin",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      headers: { "X-TTCP-Admin": "web", "Last-Event-ID": String(after) },
    });
    if (generation !== this.generation) throw new ApiError(401);
    if (!response.ok) {
      if (response.status === 401) {
        this.clear();
        this.onUnauthorized();
      }
      throw new ApiError(response.status);
    }
    if (!response.body)
      throw new Error("Log stream unavailable. Refresh to reconnect.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (!signal.aborted) {
        const chunk = await reader.read();
        if (generation !== this.generation) return;
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        let end: number;
        while ((end = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, end);
          buffer = buffer.slice(end + 2);
          if (frame.includes("event: auth_expired")) {
            this.clear();
            this.onUnauthorized();
            throw new ApiError(401);
          }
          if (frame.includes("event: gap"))
            throw new Error(
              "Earlier logs were trimmed. Refresh to load retained history.",
            );
          const data = frame
            .split("\n")
            .find((line) => line.startsWith("data: "));
          if (frame.includes("event: log") && data)
            onLog(JSON.parse(data.slice(6)));
        }
      }
    } finally {
      await reader.cancel();
      reader.releaseLock();
    }
  }
}

// This link is for sharing only; the admin app never sends credentials to the client origin.
export function invitationLink(token: string) {
  const configured = import.meta.env.VITE_CLIENT_PORTAL_URL;
  const url = configured
    ? new URL(configured)
    : new URL(window.location.origin);
  if (!configured) {
    if (!url.hostname.startsWith("admin."))
      throw new Error("Configure VITE_CLIENT_PORTAL_URL to share invitations.");
    url.hostname = url.hostname.replace(/^admin\./, "vpn.");
    if (url.port === "5174") url.port = "8080";
  }
  if (
    url.origin === window.location.origin ||
    !["http:", "https:"].includes(url.protocol)
  )
    throw new Error("Client Portal must have a separate origin.");
  url.pathname = "/";
  url.search = "";
  url.hash = "invite=" + token;
  return url.href;
}
