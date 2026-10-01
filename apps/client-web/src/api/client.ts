import type {
  Configuration,
  Device,
  Profile,
  Provision,
  Server,
} from "../domain/types";
export class ApiError extends Error {
  constructor(public status: number) {
    super(
      status === 401
        ? "Сессия истекла. Откройте новое приглашение."
        : status === 409
          ? "Операция недоступна. Проверьте квоту и состояние устройства."
          : status === 403
            ? "Нет доступа к серверу."
            : status === 404
              ? "Устройство или сервер недоступны."
              : "Не удалось выполнить запрос. Повторите попытку.",
    );
  }
}
export class ClientApi {
  private token: string | null = null;
  private generation = 0;
  onUnauthorized: () => void = () => {};
  clear() {
    this.token = null;
    this.generation++;
  }
  async request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    const generation = this.generation;
    const response = await fetch("/api/client" + path, {
      method,
      cache: "no-store",
      signal: AbortSignal.timeout(60000),
      credentials: "omit",
      referrerPolicy: "no-referrer",
      headers: {
        "Content-Type": "application/json",
        ...(this.token ? { Authorization: "Bearer " + this.token } : {}),
      },
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
    return response.status === 204 ? (undefined as T) : response.json();
  }
  async exchange(token: string) {
    const session = await this.request<{
      access_token: string;
      expires_at: string;
    }>("/exchange", "POST", { token });
    this.token = session.access_token;
    return session.expires_at;
  }
  profile = () => this.request<Profile>("/me");
  devices = () => this.request<Device[]>("/devices");
  servers = () => this.request<Server[]>("/servers");
  createDevice = (name: string, platform: string) =>
    this.request<Device>("/devices", "POST", { name, platform });
  revokeDevice = (id: string) =>
    this.request<Device>("/devices/" + id, "DELETE");
  states = (id: string) =>
    this.request<Provision[]>("/devices/" + id + "/provisioning");
  provision = (device: string, server: string) =>
    this.request("/devices/" + device + "/credentials/" + server, "POST");
  configuration = (device: string, server: string) =>
    this.request<Configuration>(
      "/devices/" + device + "/configurations/" + server,
      "POST",
    );
  async logout() {
    try {
      await this.request("/logout", "POST");
    } finally {
      this.clear();
    }
  }
}
// Read once before rendering. Fragment invitations never reach server access logs.
export function consumeInvitation() {
  const url = new URL(window.location.href);
  const token =
    new URLSearchParams(url.hash.slice(1)).get("invite") ??
    url.searchParams.get("invite");
  if (token !== null) {
    url.searchParams.delete("invite");
    url.hash = "";
    history.replaceState(null, "", url.pathname + url.search);
  }
  return token;
}
