import { useState } from "react";
import type { Server } from "../domain/types";
import type { AdminState } from "../state/useAdmin";
import { Button } from "./ui/button";
import { ConfirmAction } from "./ConfirmAction";

export function ServerLifecycle({
  node,
  admin,
}: {
  node: Server;
  admin: AdminState;
}) {
  const [version, setVersion] = useState("");
  const [email, setEmail] = useState("");
  const pending = node.lifecycle_state?.endsWith("_pending");
  const disabled = admin.busy || !node.enabled || pending;
  const ready =
    !!node.preflight_passed_at &&
    Date.now() - Date.parse(node.preflight_passed_at) < 15 * 60 * 1000;
  const validVersion =
    /^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$/.test(
      version,
    );
  return (
    <div>
      <p>
        Workload: <strong>{node.lifecycle_state || "unknown"}</strong> ·
        Installed version: {node.trusttunnel_version || "Unconfirmed"} · Desired
        version: {node.desired_trusttunnel_version || "None"}
      </p>
      {node.lifecycle_job_id && (
        <Button
          variant="outline"
          onClick={() => {
            admin.navigate("Jobs");
            admin.selectJob(node.lifecycle_job_id!);
          }}
        >
          View lifecycle job
        </Button>
      )}
      {admin.principal?.role === "admin" && (
        <>
          <p>
            {ready
              ? "Pre-flight checks passed."
              : "Run and pass pre-flight checks before deploying (valid for 15 minutes)."}
          </p>
          <label>
            TrustTunnel release version
            <input
              aria-label={`Release version for ${node.name}`}
              placeholder="1.2.3"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              disabled={disabled}
            />
          </label>
          {!node.trusttunnel_version && node.acme_http && (
            <label>
              Certificate contact email
              <input
                type="email"
                aria-label={`Certificate email for ${node.name}`}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={disabled}
              />
            </label>
          )}
          <div className="actions">
            {!node.trusttunnel_version ? (
              <Button
                disabled={
                  disabled ||
                  !ready ||
                  !validVersion ||
                  (node.acme_http && !email)
                }
                onClick={() =>
                  void admin.actions.lifecycle(
                    node.id,
                    "deploy",
                    version,
                    email || undefined,
                  )
                }
              >
                Deploy
              </Button>
            ) : (
              <>
                <ConfirmAction
                  label="Update TrustTunnel"
                  description={`Install release ${version} on ${node.name}. VPN connections may be interrupted. Configuration is preserved.`}
                  busy={disabled || !validVersion}
                  onConfirm={() =>
                    void admin.actions.lifecycle(node.id, "update", version)
                  }
                />
                <ConfirmAction
                  label="Restart TrustTunnel"
                  description={`Restart ${node.name}. Active VPN connections will be interrupted.`}
                  busy={disabled}
                  onConfirm={() =>
                    void admin.actions.lifecycle(node.id, "restart")
                  }
                />
              </>
            )}
            <ConfirmAction
              label="Uninstall TrustTunnel"
              description={`Remove the workload from ${node.name}. Revoke its VPN credentials first. Management SSH access is preserved so you can deploy again.`}
              busy={disabled}
              onConfirm={() =>
                void admin.actions.lifecycle(node.id, "uninstall")
              }
            />
          </div>
        </>
      )}
    </div>
  );
}
