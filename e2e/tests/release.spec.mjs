import { test, expect } from "playwright/test";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { diagnostics } from "../safe-stages.mjs";

const repo = fileURLToPath(new URL("../../", import.meta.url));
const port = process.env.TTCP_E2E_HTTPS_PORT ?? "18443";
if (!/^\d{1,5}$/.test(port) || Number(port) > 65535 || Number(port) < 1) {
  throw new Error("Invalid disposable HTTPS port");
}
const adminOrigin = `https://admin.localhost:${port}`;
const clientOrigin = `https://vpn.localhost:${port}`;

function cookieAttributes(cookie) {
  if (!cookie) return undefined;
  const { domain, path, secure, httpOnly, sameSite } = cookie;
  return { domain, path, secure, httpOnly, sameSite };
}

function identities() {
  if (!process.env.TTCP_E2E_FIXTURE)
    throw new Error("Run scripts/ci/run_e2e.sh first");
  return JSON.parse(readFileSync(process.env.TTCP_E2E_FIXTURE, "utf8"));
}

function compose(...args) {
  const project = process.env.TTCP_E2E_PROJECT;
  if (
    !/^ttcp-ci-e2e-[a-z0-9_-]+$/.test(project ?? "") ||
    !process.env.TTCP_E2E_ENV_FILE
  ) {
    throw new Error("Only a disposable CI Compose project is permitted");
  }
  execFileSync(
    "docker",
    [
      "--host",
      "unix:///var/run/docker.sock",
      "compose",
      "--project-name",
      project,
      "--env-file",
      process.env.TTCP_E2E_ENV_FILE,
      "-f",
      `${repo}/infra/compose/compose.ci-e2e.yaml`,
      ...args,
    ],
    { cwd: repo, stdio: "pipe", timeout: 120_000 },
  );
}

function totp(seed) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const bits = [...seed]
    .map((char) => alphabet.indexOf(char).toString(2).padStart(5, "0"))
    .join("");
  const key = Buffer.from(bits.match(/.{8}/g).map((byte) => parseInt(byte, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30_000)));
  const digest = createHmac("sha1", key).update(counter).digest();
  const offset = digest[19] & 15;
  return ((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000)
    .toString()
    .padStart(6, "0");
}

async function login(page, identity) {
  await page.goto(adminOrigin);
  await page.getByLabel("Username", { exact: true }).fill(identity.username);
  await page.getByLabel("Password", { exact: true }).fill(identity.password);
  await page.getByLabel("Authenticator code").fill(totp(identity.totp_seed));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
}

async function status(page, path, method = "GET", body) {
  return page.evaluate(
    async ({ path, method, body }) => {
      const response = await fetch(path, {
        method,
        credentials: "same-origin",
        headers: {
          "X-TTCP-Admin": "web",
          "X-TTCP-Client": "portal",
          "Content-Type": "application/json",
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      return response.status;
    },
    { path, method, body },
  );
}

async function submit(page, path, name) {
  const [response] = await Promise.all([
    page.waitForResponse(
      (item) =>
        new URL(item.url()).pathname === path &&
        item.request().method() === "POST",
    ),
    page.getByRole("button", { name, exact: true }).click(),
  ]);
  return response;
}

test("release journey, durable recovery and separate Admin/Client security boundaries", async ({
  browser,
}) => {
  const seeded = identities();
  const diagnostic = diagnostics();
  const stage = (label) => diagnostic.checkpoint(label);
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const unexpectedNetwork = [];
  // Egress guard only; application API responses are never mocked.
  const guard = (route) => {
    const origin = new URL(route.request().url()).origin;
    if ([adminOrigin, clientOrigin].includes(origin)) return route.continue();
    unexpectedNetwork.push(true);
    return route.abort();
  };
  await context.route("**/*", guard);
  try {
    const admin = await context.newPage();
    stage("admin-login");
    await login(admin, seeded.admin);
    stage("admin-refresh");
    await admin.reload();
    await expect(
      admin.getByRole("heading", { name: "Dashboard", exact: true }),
    ).toBeVisible();
    const cookie = (await context.cookies(adminOrigin)).find(
      (item) => item.name === "__Host-ttcp_admin",
    );
    expect(cookieAttributes(cookie)).toEqual({
      domain: "admin.localhost",
      path: "/",
      secure: true,
      httpOnly: true,
      sameSite: "Strict",
    });
    stage("server-form");
    await admin.getByRole("button", { name: "Servers", exact: true }).click();
    await admin.getByText("Add managed server", { exact: true }).click();
    for (const [label, value] of [
      ["Server name", "ci-node"],
      ["SSH hostname", "managed-node.invalid"],
      ["Public IP", "192.0.2.10"],
      ["VPN domain", "vpn-node.invalid"],
      ["Management SSH user", "ci_manager"],
      ["Pinned SSH host public key", seeded.ssh.host_key],
      ["Management OpenSSH private key", seeded.ssh.private_key],
    ])
      await admin.getByLabel(label, { exact: true }).fill(value);
    stage("server-submit");
    const serverResponse = await submit(admin, "/api/servers", "Create server");
    expect(serverResponse.status()).toBe(201);
    const serverId = (await serverResponse.json()).id;
    await expect(
      admin.getByRole("heading", { name: "ci-node", exact: true }),
    ).toBeVisible();
    stage("user-form");
    await admin.getByRole("button", { name: "VPN users", exact: true }).click();
    await admin.getByText("Add VPN user", { exact: true }).click();
    await admin.getByLabel("Display name", { exact: true }).fill("CI Client");
    await admin.getByLabel("Device limit", { exact: true }).fill("1");
    stage("user-submit");
    const userResponse = await submit(
      admin,
      "/api/vpn-users",
      "Create VPN user",
    );
    expect(userResponse.status()).toBe(201);
    const userId = (await userResponse.json()).id;
    stage("user-select");
    await admin.getByLabel("VPN user", { exact: true }).selectOption(userId);
    stage("access-grant");
    await admin
      .getByRole("combobox", { name: /^Access mode/ })
      .selectOption("all");
    await admin
      .getByRole("button", { name: "Save access", exact: true })
      .click();
    await admin
      .getByRole("alertdialog")
      .getByRole("button", { name: "Confirm", exact: true })
      .click();
    await expect(
      admin.getByText("All enabled servers, including future servers", {
        exact: false,
      }),
    ).toBeVisible();
    stage("invitation-create");
    await admin
      .getByRole("button", { name: "Invitations", exact: true })
      .click();
    await admin.getByLabel("VPN user", { exact: true }).selectOption(userId);
    await admin
      .getByRole("button", { name: "Create invitation", exact: true })
      .click();
    const invite = admin.getByLabel("Private invitation link", { exact: true });
    await expect(invite).toBeVisible();
    const invitation = await invite.inputValue();
    expect(new URL(invitation).origin).toBe(clientOrigin);

    const client = await context.newPage();
    stage("client-exchange");
    await client.goto(invitation);
    await expect(
      client.getByRole("heading", { name: "CI Client", exact: true }),
    ).toBeVisible();
    expect(new URL(client.url()).hash).toBe("");
    stage("invitation-replay");
    const replay = await context.request.post(
      `${clientOrigin}/api/client/exchange`,
      {
        data: {
          token: new URLSearchParams(new URL(invitation).hash.slice(1)).get(
            "invite",
          ),
        },
      },
    );
    expect(replay.status()).toBe(401);
    stage("client-refresh");
    await client.reload();
    await expect(
      client.getByRole("heading", { name: "CI Client", exact: true }),
    ).toBeVisible();
    const clientCookies = await context.cookies(
      `${clientOrigin}/api/client/me`,
    );
    expect(
      clientCookies.some((item) => item.name === "__Host-ttcp_admin"),
    ).toBe(false);
    expect(
      cookieAttributes(
        clientCookies.find((item) => item.name === "__Secure-ttcp_client"),
      ),
    ).toEqual({
      domain: "vpn.localhost",
      path: "/api/client",
      secure: true,
      httpOnly: true,
      sameSite: "Strict",
    });
    stage("device-create");
    await client
      .getByLabel("Название нового устройства", { exact: true })
      .fill("CI Phone");
    await client
      .getByRole("combobox", { name: /^Платформа/ })
      .selectOption("Android");
    const deviceResponse = await submit(
      client,
      "/api/client/devices",
      "Добавить устройство",
    );
    expect(deviceResponse.status()).toBe(201);
    const deviceId = (await deviceResponse.json()).id;
    stage("device-select");
    await client
      .getByRole("combobox", { name: /^Ваше устройство/ })
      .selectOption(deviceId);
    stage("server-select");
    await client
      .getByRole("combobox", { name: /^Сервер подключения/ })
      .selectOption(serverId);
    await expect(
      client.getByText("Устройства: 1 из 1", { exact: false }),
    ).toBeVisible();
    stage("quota-denial");
    expect(
      await status(client, "/api/client/devices", "POST", {
        name: "Over quota",
      }),
    ).toBe(409);

    // Persist intent while the worker is stopped, then lose all ephemeral Redis state.
    stage("worker-stop");
    compose("stop", "worker");
    stage("credential-queue");
    await client
      .getByRole("button", { name: "Настроить подключение", exact: true })
      .click();
    await expect(client.getByText("В очереди", { exact: true })).toBeVisible();
    stage("redis-restart");
    compose("restart", "redis");
    compose("up", "-d", "--wait", "--wait-timeout", "60", "redis");
    stage("worker-start");
    compose("up", "-d", "worker");
    stage("configuration-ready");
    await expect(
      client.getByRole("button", {
        name: "Получить конфигурацию",
        exact: true,
      }),
    ).toBeEnabled();
    stage("configuration-delivery");
    await client
      .getByRole("button", { name: "Получить конфигурацию", exact: true })
      .click();
    const deliveryLink = client.getByRole("link", {
      name: "Открыть в TrustTunnel",
      exact: true,
    });
    await expect(deliveryLink).toBeVisible();
    expect((await deliveryLink.getAttribute("href"))?.startsWith("tt://")).toBe(
      true,
    );
    await client
      .getByText("Другие способы подключения", { exact: true })
      .click();
    await expect(
      client.getByRole("img", {
        name: "QR-код подключения TrustTunnel",
        exact: true,
      }),
    ).toBeVisible();
    stage("configuration-download");
    const [file] = await Promise.all([
      client.waitForEvent("download"),
      client.getByRole("button", { name: "Скачать TOML", exact: true }).click(),
    ]);
    expect(file.suggestedFilename()).toBe("trusttunnel.toml");
    const stream = await file.createReadStream();
    const chunks = [];
    for await (const chunk of stream) chunks.push(chunk);
    const config = Buffer.concat(chunks).toString("utf8");
    expect(config.includes('endpoint = "vpn-node.invalid"')).toBe(true);
    expect(/username = "ttcp_[a-f0-9]{32}"/.test(config)).toBe(true);
    expect(/password = "[A-Za-z0-9_-]{32,128}"/.test(config)).toBe(true);
    for (const page of [admin, client]) {
      expect(
        await page.evaluate(() => localStorage.length + sessionStorage.length),
      ).toBe(0);
    }

    // Wrong-origin routes are denied by the production NGINX allowlist before the API.
    stage("origin-denials");
    for (const path of [
      "/api/auth/me",
      "/api/servers",
      "/api/vpn-users",
      "/api/jobs",
      "/api/backups",
    ]) {
      expect(await status(client, path)).toBe(404);
    }
    for (const path of [
      "/api/client/me",
      "/api/client/devices",
      "/api/client/exchange",
    ]) {
      expect(await status(admin, path)).toBe(404);
    }
    expect(await status(admin, "/api/unknown")).toBe(404);
    expect(await status(client, "/api/client/me", "POST", {})).toBe(403);
    const csrf = await context.request.get(`${adminOrigin}/api/auth/me`, {
      headers: {
        "X-TTCP-Admin": "web",
        Origin: clientOrigin,
        "Sec-Fetch-Site": "same-site",
      },
    });
    expect(csrf.status()).toBe(403);
    const preflight = await context.request.fetch(
      `${adminOrigin}/api/vpn-users`,
      {
        method: "OPTIONS",
        headers: {
          Origin: clientOrigin,
          "Access-Control-Request-Method": "POST",
        },
      },
    );
    expect(preflight.status()).toBe(403);
    expect(preflight.headers()["access-control-allow-origin"]).toBeUndefined();
    expect(
      await client.evaluate(async (origin) => {
        try {
          await fetch(`${origin}/api/vpn-users`, {
            credentials: "include",
            headers: { "X-TTCP-Admin": "web" },
          });
          return false;
        } catch {
          return true;
        }
      }, adminOrigin),
    ).toBe(true);

    stage("device-revoke");
    await client
      .getByRole("button", { name: "Отозвать устройство", exact: true })
      .click();
    await client
      .getByRole("alertdialog")
      .getByRole("button", { name: "Да, отозвать", exact: true })
      .click();
    await expect(
      client.getByText("CI Phone — отозвано", { exact: true }),
    ).toBeVisible();
    expect(
      await status(
        client,
        `/api/client/devices/${deviceId}/configurations/${serverId}`,
        "POST",
      ),
    ).toBe(409);
    stage("credential-revoke");
    await expect
      .poll(async () => {
        const response = await context.request.get(
          `${adminOrigin}/api/vpn-users/${userId}/devices/${deviceId}/credentials`,
          { headers: { "X-TTCP-Admin": "web" } },
        );
        return response.ok()
          ? (await response.json())[0]?.status
          : `http-${response.status()}`;
      })
      .toBe("revoked");
    stage("audit-check");
    for (const action of [
      "client.configuration.deliver",
      "credential.create.applied",
      "credential.revoke.applied",
    ]) {
      const audit = await context.request.get(
        `${adminOrigin}/api/audit?action=${action}`,
        {
          headers: { "X-TTCP-Admin": "web" },
        },
      );
      expect(audit.status()).toBe(200);
      const entries = (await audit.json()).items;
      expect(entries.length).toBeGreaterThan(0);
      expect(
        entries.every(
          (entry) => entry.result === "success" && entry.request_id,
        ),
      ).toBe(true);
    }
    stage("client-logout");
    expect((await submit(client, "/api/client/logout", "Выйти")).status()).toBe(
      204,
    );
    await client.reload();
    await expect(
      client.getByRole("heading", { name: "Вход по приглашению", exact: true }),
    ).toBeVisible();

    const viewerContext = await browser.newContext({ ignoreHTTPSErrors: true });
    await viewerContext.route("**/*", guard);
    try {
      const viewer = await viewerContext.newPage();
      stage("viewer-login");
      await login(viewer, seeded.viewer);
      stage("viewer-denials");
      expect(
        await status(viewer, "/api/vpn-users", "POST", {
          display_name: "Forbidden",
        }),
      ).toBe(403);
      expect(
        await status(
          viewer,
          `/api/vpn-users/${userId}/invitations`,
          "POST",
          {},
        ),
      ).toBe(403);
      expect(
        await status(viewer, `/api/servers/${serverId}/enabled`, "PUT", {
          enabled: false,
        }),
      ).toBe(403);
      expect(
        await status(viewer, "/api/backups/run", "POST", {
          idempotency_key: "ci-denied",
        }),
      ).toBe(403);
    } finally {
      await viewerContext.close().catch(() => {});
    }
    stage("admin-logout");
    expect((await submit(admin, "/api/auth/logout", "Sign out")).status()).toBe(
      204,
    );
    await admin.reload();
    await expect(
      admin.getByRole("heading", { name: "Sign in", exact: true }),
    ).toBeVisible();
    expect(unexpectedNetwork).toEqual([]);
    stage("complete");
  } catch {
    throw diagnostic.failure();
  } finally {
    // The runner owns browser disposal; avoid replacing the primary stage failure
    // when Playwright already closed the context at its deadline.
    await context.close().catch(() => {});
  }
});
