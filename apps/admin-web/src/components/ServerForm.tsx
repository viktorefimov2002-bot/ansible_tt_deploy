import { useState } from "react";
import type { Server, ServerInput } from "../domain/types";
import { Button } from "./ui/button";
export function ServerForm({
  node,
  busy,
  onSave,
}: {
  node?: Server;
  busy: boolean;
  onSave: (
    body: ServerInput,
    id?: string,
    hostKey?: string,
    privateKey?: string,
  ) => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <details
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>{node ? "Edit server details" : "Add managed server"}</summary>
      <form
        className="form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          const form = event.currentTarget;
          const values = new FormData(form);
          const text = (key: string) => String(values.get(key) ?? "");
          const body: ServerInput = {
            name: text("name"),
            hostname: text("hostname"),
            public_ip: text("public_ip"),
            domain: text("domain"),
            location: text("location") || null,
            ssh_user: text("ssh_user"),
            ssh_port: Number(text("ssh_port")),
            acme_http: values.has("acme_http"),
          };
          const pending = onSave(
            body,
            node?.id,
            text("host_key"),
            text("private_key"),
          );
          if (!node) {
            form.reset();
            setExpanded(false);
          } // Clear SSH material immediately after handoff.
          void pending;
        }}
      >
        <fieldset disabled={busy}>
          {(
            [
              ["name", "Server name", "text"],
              ["hostname", "SSH hostname", "text"],
              ["public_ip", "Public IP", "text"],
              ["domain", "VPN domain", "text"],
              ["location", "Location", "text"],
              ["ssh_user", "Management SSH user", "text"],
              ["ssh_port", "SSH port", "number"],
            ] as const
          ).map(([name, label, type]) => (
            <label key={name}>
              {label}
              <input
                name={name}
                type={type}
                required={name !== "location"}
                defaultValue={node?.[name] ?? (name === "ssh_port" ? 22 : "")}
                min={type === "number" ? 1 : undefined}
                max={type === "number" ? 65535 : undefined}
              />
            </label>
          ))}
          <label className="check">
            <input
              type="checkbox"
              name="acme_http"
              defaultChecked={node?.acme_http ?? true}
            />
            Check HTTP ACME readiness
          </label>
          {!node && (
            <>
              <p>
                Use an already bootstrapped management account. Verify the host
                key through a trusted channel.
              </p>
              <label>
                Pinned SSH host public key
                <input name="host_key" required autoComplete="off" />
              </label>
              <label>
                Management OpenSSH private key
                <textarea
                  name="private_key"
                  required
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>
            </>
          )}
          <Button type="submit">
            {node ? "Save server" : "Create server"}
          </Button>
        </fieldset>
      </form>
    </details>
  );
}
