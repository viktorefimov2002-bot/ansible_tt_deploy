import { useState } from "react";
import type { AdminState } from "../state/useAdmin";
import type { UserInput, VpnUser } from "../domain/types";
import { Button } from "./ui/button";
import { ConfirmAction } from "./ConfirmAction";

export function UserForm({
  person,
  busy,
  onSave,
}: {
  person?: VpnUser;
  busy: boolean;
  onSave: (body: UserInput, id?: string) => Promise<void>;
}) {
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        const date = String(data.get("expires") ?? "");
        void onSave(
          {
            display_name: String(data.get("name")),
            device_limit: Number(data.get("limit")),
            expires_at: date ? new Date(date).toISOString() : null,
          },
          person?.id,
        );
        if (!person) form.reset();
      }}
    >
      <fieldset disabled={busy}>
        <label>
          Display name
          <input
            name="name"
            required
            maxLength={255}
            defaultValue={person?.display_name}
          />
        </label>
        <label>
          Device limit
          <input
            name="limit"
            type="number"
            min={0}
            required
            defaultValue={person?.device_limit ?? 3}
          />
        </label>
        <label>
          Access expires (your local time)
          <input
            name="expires"
            type="datetime-local"
            defaultValue={
              person?.expires_at ? localDate(person.expires_at) : ""
            }
          />
        </label>
        <Button type="submit">
          {person ? "Save user" : "Create VPN user"}
        </Button>
      </fieldset>
    </form>
  );
}
function localDate(value: string) {
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
export function UserDetails({ admin }: { admin: AdminState }) {
  const person = admin.user!;
  const writable = admin.principal?.role === "admin";
  const [mode, setMode] = useState(person.access_mode);
  const [selected, setSelected] = useState(person.server_ids);
  const used = admin.devices.filter((device) => device.enabled).length;
  const eligible = admin.servers.filter(
    (node) =>
      node.enabled &&
      (person.access_mode === "all" || person.server_ids.includes(node.id)),
  );
  const available =
    person.enabled &&
    (!person.expires_at || Date.parse(person.expires_at) > Date.now());
  return (
    <section className="panel">
      <h2>{person.display_name}</h2>
      <p>
        {person.enabled ? "Enabled" : "Disabled"} ·{" "}
        {person.expires_at
          ? `Expires ${new Date(person.expires_at).toLocaleString()}`
          : "No expiration"}
      </p>
      {writable && (
        <>
          <details>
            <summary>Edit user</summary>
            <UserForm
              person={person}
              busy={admin.busy}
              onSave={admin.actions.saveUser}
            />
          </details>
          {person.enabled ? (
            <ConfirmAction
              label="Disable user"
              description="All devices and server credentials will be revoked. Re-enabling the user does not restore them."
              busy={admin.busy}
              onConfirm={() => void admin.actions.userEnabled(person)}
            />
          ) : (
            <Button
              disabled={admin.busy}
              onClick={() => void admin.actions.userEnabled(person)}
            >
              Enable user
            </Button>
          )}
        </>
      )}
      <h3>Server access</h3>
      <p>
        Current access:{" "}
        {person.access_mode === "all"
          ? "All enabled servers, including future servers"
          : person.server_ids
              .map(
                (id) =>
                  admin.servers.find((node) => node.id === id)?.name ?? id,
              )
              .join(", ") || "No servers"}
      </p>
      {writable && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
          }}
        >
          <fieldset disabled={admin.busy}>
            <label>
              Access mode
              <select
                value={mode}
                onChange={(event) =>
                  setMode(event.target.value as "selected" | "all")
                }
              >
                <option value="selected">Selected servers</option>
                <option value="all">All servers</option>
              </select>
            </label>
            {mode === "selected" &&
              admin.servers.map((node) => (
                <label className="check" key={node.id}>
                  <input
                    type="checkbox"
                    disabled={!node.enabled && !selected.includes(node.id)}
                    checked={selected.includes(node.id)}
                    onChange={(event) =>
                      setSelected((ids) =>
                        event.target.checked
                          ? [...ids, node.id]
                          : ids.filter((id) => id !== node.id),
                      )
                    }
                  />
                  {node.name}
                  {!node.enabled && " (disabled)"}
                </label>
              ))}
            <p>
              Removing access schedules credential revocation for removed
              servers.
            </p>
            <ConfirmAction
              label="Save access"
              description="Changing access can revoke credentials on removed servers. Existing configurations for those servers will stop working."
              busy={admin.busy}
              onConfirm={() =>
                void admin.actions.access(
                  person.id,
                  mode,
                  mode === "all" ? [] : selected,
                )
              }
            />
          </fieldset>
        </form>
      )}
      <h3>Devices and credentials</h3>
      {admin.detailsLoading && <p role="status">Loading device states…</p>}
      <p>
        Devices: {used} of {person.device_limit}. Credentials on other servers
        do not consume device quota.
      </p>
      {writable && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            void admin.actions.createDevice(
              person.id,
              String(data.get("name")),
              String(data.get("platform")),
            );
            event.currentTarget.reset();
          }}
        >
          <fieldset
            disabled={
              admin.busy ||
              admin.detailsLoading ||
              !available ||
              used >= person.device_limit
            }
          >
            <label>
              New device name
              <input name="name" required maxLength={255} />
            </label>
            <label>
              Platform
              <input name="platform" maxLength={64} />
            </label>
            <Button type="submit">Add device</Button>
          </fieldset>
        </form>
      )}
      {!admin.detailsLoading && !admin.devices.length && <p>No devices yet.</p>}
      {admin.devices.map((device) => (
        <article className="device" key={device.id}>
          <h4>
            {device.name} · {device.enabled ? "Enabled" : "Revoked"}
          </h4>
          <p>{device.platform || "Platform unspecified"}</p>
          {writable && device.enabled && (
            <ConfirmAction
              label={`Revoke device ${device.name}`}
              description="Every server credential for this device will be revoked."
              busy={admin.busy}
              onConfirm={() =>
                void admin.actions.revokeDevice(person.id, device.id)
              }
            />
          )}
          {admin.credentials
            .filter((credential) => credential.device_id === device.id)
            .map((credential) => (
              <div className="credential" key={credential.id}>
                <span>
                  {admin.servers.find(
                    (node) => node.id === credential.server_id,
                  )?.name ?? credential.server_id}
                  : <strong>{credential.status}</strong>
                </span>
                {writable &&
                  !["revoked", "revoking"].includes(credential.status) && (
                    <ConfirmAction
                      label="Revoke credential"
                      description="This connection will stop working after revocation is applied."
                      busy={admin.busy}
                      onConfirm={() =>
                        void admin.actions.credential(
                          person.id,
                          device.id,
                          credential.server_id,
                          true,
                        )
                      }
                    />
                  )}
              </div>
            ))}
          {writable &&
            device.enabled &&
            available &&
            eligible.map((node) => {
              const credential = admin.credentials.find(
                (item) =>
                  item.device_id === device.id && item.server_id === node.id,
              );
              const ready = credential?.status === "active";
              return (
                <Button
                  key={node.id}
                  variant="outline"
                  disabled={
                    admin.busy ||
                    ["pending", "revoking"].includes(credential?.status ?? "")
                  }
                  onClick={() =>
                    void admin.actions.credential(person.id, device.id, node.id)
                  }
                >
                  {ready
                    ? "Ensure configuration"
                    : credential?.status === "failed"
                      ? "Retry"
                      : "Provision"}{" "}
                  {node.name}
                </Button>
              );
            })}
        </article>
      ))}
    </section>
  );
}
