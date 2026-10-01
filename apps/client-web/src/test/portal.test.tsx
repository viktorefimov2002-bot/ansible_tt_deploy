import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { consumeInvitation } from "../api/client";
import { usePortal } from "../state/usePortal";
import { PortalView } from "../components/PortalView";
import type { ProvisionState } from "../domain/types";
let devices: { id: string; name: string; enabled: boolean }[];
let state: ProvisionState | undefined;
let deny: boolean;
let requests: string[];
const token = "S".repeat(43);
function App() {
  return <PortalView portal={usePortal("I".repeat(43))} />;
}
beforeEach(() => {
  devices = [];
  state = undefined;
  deny = false;
  requests = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit) => {
      const path = url.replace("/api/client", "");
      requests.push(path);
      const response = (data: unknown, status = 200) =>
        new Response(JSON.stringify(data), { status });
      expect(options.cache).toBe("no-store");
      if (path === "/exchange")
        return response({
          access_token: token,
          expires_at: new Date(Date.now() + 60000).toISOString(),
        });
      expect((options.headers as Record<string, string>).Authorization).toBe(
        "Bearer " + token,
      );
      if (deny) return response({}, 401);
      if (path === "/me")
        return response({
          id: "u",
          display_name: "Анна",
          device_limit: 1,
          devices_used: devices.filter((d) => d.enabled).length,
          expires_at: null,
        });
      if (path === "/servers")
        return response([{ id: "s", name: "Finland", location: "Helsinki" }]);
      if (path === "/devices" && options.method === "POST") {
        devices.push({ id: "d", name: "Телефон", enabled: true });
        return response(devices[0]);
      }
      if (path === "/devices") return response(devices);
      if (path.endsWith("/provisioning"))
        return response(state ? [{ server_id: "s", state }] : []);
      if (path.includes("/credentials/")) {
        state = "running";
        return response({ id: "j", status: "queued" });
      }
      if (path.includes("/configurations/"))
        return response({
          toml: "# synthetic config",
          deep_link: "tt://synthetic",
        });
      if (options.method === "DELETE") {
        devices[0].enabled = false;
        state = "revoked";
        return response(devices[0]);
      }
      throw new Error("Unexpected request");
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
test("invitation removed from URL before exchange and never stored", () => {
  history.replaceState(null, "", "/client/#invite=" + "I".repeat(43));
  expect(consumeInvitation()).toBe("I".repeat(43));
  expect(location.hash).toBe("");
  expect(consumeInvitation()).toBe(null);
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});
test("invite → device → running → ready → config → confirmed revoke", async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByText("Анна");
  await user.type(
    screen.getByLabelText("Название нового устройства"),
    "Телефон",
  );
  await user.click(screen.getByRole("button", { name: "Добавить устройство" }));
  await screen.findByText("Устройства: 1 из 1");
  expect(
    screen.getByRole("button", { name: "Добавить устройство" }),
  ).toBeDisabled();
  await user.click(
    screen.getByRole("button", { name: "Настроить подключение" }),
  );
  await screen.findByText("Настраиваем подключение");
  state = "ready";
  await screen.findByText("Готово к подключению", {}, { timeout: 4500 });
  await user.click(
    screen.getByRole("button", { name: "Получить конфигурацию" }),
  );
  expect(
    await screen.findByRole("link", { name: "Открыть в TrustTunnel" }),
  ).toHaveAttribute("href", "tt://synthetic");
  await user.click(screen.getByText("Другие способы подключения"));
  expect(
    await screen.findByAltText("QR-код подключения TrustTunnel"),
  ).toHaveAttribute("src", expect.stringContaining("data:image/png"));
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const objectUrl = vi.fn(() => "blob:test");
  URL.createObjectURL = objectUrl;
  URL.revokeObjectURL = vi.fn();
  await user.click(screen.getByRole("button", { name: "Скачать TOML" }));
  expect(objectUrl).toHaveBeenCalledWith(expect.any(Blob));
  expect(click).toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Копировать конфигурацию" }),
  );
  await screen.findByText("Конфигурация скопирована.");
  expect(await navigator.clipboard.readText()).toBe("# synthetic config");
  await user.click(screen.getByRole("button", { name: "Отозвать устройство" }));
  expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Отмена" }));
  expect(devices[0].enabled).toBe(true);
  await user.click(screen.getByRole("button", { name: "Отозвать устройство" }));
  await user.click(screen.getByRole("button", { name: "Да, отозвать" }));
  await screen.findByText("Телефон — отозвано");
  expect(
    screen.queryByRole("link", { name: "Открыть в TrustTunnel" }),
  ).toBeNull();
  expect(localStorage.length + sessionStorage.length).toBe(0);
});
test("failed provisioning can be retried and expired session clears config", async () => {
  devices = [{ id: "d", name: "Телефон", enabled: true }];
  state = "failed";
  const user = userEvent.setup();
  render(<App />);
  await screen.findByText("Не удалось настроить. Повторите попытку.");
  await user.click(screen.getByRole("button", { name: "Повторить настройку" }));
  await screen.findByText("Настраиваем подключение");
  state = "ready";
  await screen.findByText("Готово к подключению", {}, { timeout: 4500 });
  await user.click(
    screen.getByRole("button", { name: "Получить конфигурацию" }),
  );
  await screen.findByRole("link", { name: "Открыть в TrustTunnel" });
  deny = true;
  await screen.findByText(
    "Сессия истекла. Откройте новое приглашение.",
    {},
    { timeout: 4500 },
  );
  expect(
    screen.queryByRole("link", { name: "Открыть в TrustTunnel" }),
  ).toBeNull();
  expect(screen.queryByText("Анна")).toBeNull();
});
test("configuration response cannot reappear after switching devices", async () => {
  devices = [
    { id: "d", name: "Телефон", enabled: true },
    { id: "e", name: "Ноутбук", enabled: true },
  ];
  state = "ready";
  const original = fetch;
  let resolve: (response: Response) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, options: RequestInit) =>
      url.includes("/configurations/")
        ? new Promise<Response>((r) => {
            resolve = r;
          })
        : original(url, options),
    ),
  );
  const user = userEvent.setup();
  render(<App />);
  await screen.findByText("Готово к подключению");
  await user.click(
    screen.getByRole("button", { name: "Получить конфигурацию" }),
  );
  // A response from the selected pair must be cleared before changing the pair.
  resolve(
    new Response(
      JSON.stringify({ toml: "# config", deep_link: "tt://synthetic" }),
    ),
  );
  await screen.findByRole("link", { name: "Открыть в TrustTunnel" });
  await user.selectOptions(screen.getByLabelText("Ваше устройство"), "e");
  await waitFor(() =>
    expect(
      screen.queryByRole("link", { name: "Открыть в TrustTunnel" }),
    ).toBeNull(),
  );
  expect(requests.some((x) => x.endsWith("/provisioning"))).toBe(true);
});
