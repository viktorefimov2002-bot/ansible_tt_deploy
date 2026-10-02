import { useRef, useState } from "react";
import type { Server, ServerConfiguration as Settings } from "../domain/types";
import {
  configurationState,
  defaultConfiguration,
  timeoutFields,
} from "../domain/configuration";
import type { AdminState } from "../state/useAdmin";
import { Button } from "./ui/button";
import { ConfirmAction } from "./ConfirmAction";

export function ServerConfiguration({
  node,
  admin,
}: {
  node: Server;
  admin: AdminState;
}) {
  const [draft, setDraft] = useState<Settings>({ ...defaultConfiguration });
  const [key, setKey] = useState(() => crypto.randomUUID());
  const applyKeys = useRef(new Map<string, string>());
  const selected = admin.configServerId === node.id;
  const writable = admin.principal?.role === "admin";
  const pending =
    node.config_state === "apply_pending" ||
    node.lifecycle_state?.endsWith("_pending");
  const disabled = admin.busy || pending;
  const installed = !!node.trusttunnel_version && node.enabled;
  function change(config: Settings) {
    setDraft(config);
    setKey(crypto.randomUUID());
  }
  function apply(revision: string) {
    let key = applyKeys.current.get(revision);
    if (!key) {
      key = crypto.randomUUID();
      applyKeys.current.set(revision, key);
    }
    return admin.actions.applyConfigRevision(node.id, revision, key);
  }
  return (
    <section aria-label={`Configuration for ${node.name}`}>
      <h3>Server configuration</h3>
      <p>
        {configurationState(node.config_state || "idle")} · Current revision:{" "}
        {node.config_revision_id
          ? admin.configRevisions?.find((r) => r.id === node.config_revision_id)
              ?.revision || "Recorded"
          : "Deployment baseline"}
      </p>
      <Button
        variant="outline"
        onClick={() => admin.selectConfigServer(selected ? "" : node.id)}
      >
        {selected ? "Close configuration history" : "Configuration revisions"}
      </Button>
      {node.config_job_id && (
        <Button
          variant="outline"
          onClick={() => {
            admin.navigate("Jobs");
            admin.selectJob(node.config_job_id!);
          }}
        >
          View configuration job
        </Button>
      )}
      {selected && (
        <>
          {writable && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void admin.actions.createConfigRevision(node.id, draft, key);
              }}
            >
              <h4>Create a revision</h4>
              <p>
                Saved settings are validated and immutable. Apply separately to
                activate them. Credentials and existing VPN users are preserved.
              </p>
              <fieldset disabled={disabled}>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={draft.ipv6_available}
                    onChange={(e) =>
                      change({ ...draft, ipv6_available: e.target.checked })
                    }
                  />
                  Allow IPv6 destinations
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={draft.allow_private_network_connections}
                    onChange={(e) =>
                      change({
                        ...draft,
                        allow_private_network_connections: e.target.checked,
                      })
                    }
                  />
                  Allow connections to private networks
                </label>
                <details>
                  <summary>Advanced connection timeouts</summary>
                  {timeoutFields.map(([field, label, maximum]) => (
                    <label key={field}>
                      {label} (seconds)
                      <input
                        type="number"
                        min={1}
                        max={maximum}
                        step={1}
                        required
                        value={draft[field]}
                        onChange={(e) =>
                          change({ ...draft, [field]: Number(e.target.value) })
                        }
                      />
                    </label>
                  ))}
                </details>
                <Button type="submit">Create revision</Button>
              </fieldset>
            </form>
          )}
          {admin.configLoading && <p role="status">Loading revisions…</p>}
          {admin.configError && <p role="alert">{admin.configError}</p>}
          {!admin.configLoading &&
            !admin.configError &&
            !admin.configRevisions.length && (
              <p>
                No revisions on this page. The deployment baseline is preserved
                until apply.
              </p>
            )}
          {admin.configRevisions.map((revision) => (
            <article className="device" key={revision.id}>
              <h4>
                Revision {revision.revision}
                {node.config_revision_id === revision.id && " · Current"}
              </h4>
              <p>{configurationState(revision.status)}</p>
              <p>Created {new Date(revision.created_at).toLocaleString()}</p>
              {revision.applied_at && (
                <p>Applied {new Date(revision.applied_at).toLocaleString()}</p>
              )}
              {revision.failure_message && (
                <p role="alert">{revision.failure_message}</p>
              )}
              {revision.config ? (
                <details>
                  <summary>Revision settings</summary>
                  <dl>
                    <dt>IPv6 destinations</dt>
                    <dd>
                      {revision.config.ipv6_available ? "Allowed" : "Disabled"}
                    </dd>
                    <dt>Private networks</dt>
                    <dd>
                      {revision.config.allow_private_network_connections
                        ? "Allowed"
                        : "Blocked"}
                    </dd>
                    {timeoutFields.map(([field, label]) => (
                      <div key={field}>
                        <dt>{label}</dt>
                        <dd>{revision.config![field]} seconds</dd>
                      </div>
                    ))}
                  </dl>
                </details>
              ) : (
                <p>
                  This historical revision has invalid settings. Create a
                  validated revision before applying.
                </p>
              )}
              <div className="actions">
                {revision.job_id && (
                  <Button
                    variant="outline"
                    onClick={() => {
                      admin.navigate("Jobs");
                      admin.selectJob(revision.job_id!);
                    }}
                  >
                    View revision {revision.revision} job
                  </Button>
                )}
                {writable && revision.config && (
                  <Button
                    variant="outline"
                    disabled={disabled}
                    onClick={() => change({ ...revision.config! })}
                  >
                    Use revision {revision.revision} settings
                  </Button>
                )}
                {writable &&
                  !revision.job_id &&
                  revision.config &&
                  revision.status === "validated" && (
                    <ConfirmAction
                      label={`Apply revision ${revision.revision}`}
                      description={`Apply these settings to ${node.name} and restart TrustTunnel. VPN connections may be interrupted. If apply fails, the previous configuration will be restored automatically.`}
                      busy={disabled || !installed}
                      onConfirm={() => void apply(revision.id)}
                    />
                  )}
              </div>
            </article>
          ))}
          <div className="actions">
            <Button
              variant="outline"
              disabled={admin.configLoading || admin.configOffset === 0}
              onClick={() =>
                admin.pageConfig(Math.max(0, admin.configOffset - 50))
              }
            >
              Newer revisions
            </Button>
            <Button
              variant="outline"
              disabled={
                admin.configLoading || admin.configRevisions.length < 50
              }
              onClick={() => admin.pageConfig(admin.configOffset + 50)}
            >
              Older revisions
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
