import { useRef } from "react";
import type { AdminState } from "../state/useAdmin";
import { Button } from "./ui/button";

export function BackupsView({ admin }: { admin: AdminState }) {
  const data = admin.backups;
  const requestKey = useRef<string | null>(null);
  const active =
    data?.latest && ["queued", "running"].includes(data.latest.job_status);
  return (
    <section className="panel">
      <h2>Remote PostgreSQL backups</h2>
      {admin.backupsLoading && <p role="status">Loading backups…</p>}
      {admin.backupsError && <p role="alert">{admin.backupsError}</p>}
      {data && (
        <>
          {!data.enabled && (
            <p>Remote backups are not configured. Contact the operator.</p>
          )}
          <p>
            Latest backup: <strong>{data.latest?.status ?? "Never run"}</strong>
          </p>
          <p>
            Last verified backup:{" "}
            {data.last_success?.finished_at
              ? new Date(data.last_success.finished_at).toLocaleString()
              : "None"}
          </p>
          <p>
            Snapshot age:{" "}
            {data.age_seconds === null
              ? "Unavailable"
              : `${Math.floor(data.age_seconds / 60)} minutes`}
          </p>
          {data.latest?.status === "unknown" && (
            <p role="alert">
              The remote outcome is unknown. An operator must verify the archive
              using the maintenance CLI.
            </p>
          )}
          {admin.principal?.role === "admin" && (
            <Button
              disabled={admin.busy || !!active || !data.enabled}
              onClick={() => {
                const key = requestKey.current ?? crypto.randomUUID();
                requestKey.current = key;
                void admin.actions.runBackup(key).then((accepted) => {
                  if (accepted) requestKey.current = null;
                });
              }}
            >
              Run backup now
            </Button>
          )}
          <h3>History</h3>
          {!data.items.length && <p>No backups yet.</p>}
          {data.items.map((backup) => (
            <article className="notification" key={backup.id}>
              <p>
                <strong>{backup.status}</strong> ·{" "}
                {new Date(backup.created_at).toLocaleString()}
              </p>
              <p>
                Attempts: {backup.attempts} / {backup.max_attempts}
              </p>
              <Button
                variant="outline"
                onClick={() => {
                  admin.navigate("Jobs");
                  admin.selectJob(backup.id);
                }}
              >
                Inspect backup job
              </Button>
            </article>
          ))}
          <div className="actions">
            <Button
              variant="outline"
              disabled={admin.backupsLoading || admin.backupOffset === 0}
              onClick={() =>
                admin.setBackupOffset(Math.max(0, admin.backupOffset - 50))
              }
            >
              Previous backups
            </Button>
            <Button
              variant="outline"
              disabled={
                admin.backupsLoading ||
                admin.backupOffset + data.items.length >= data.total
              }
              onClick={() => admin.setBackupOffset(admin.backupOffset + 50)}
            >
              Next backups
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
