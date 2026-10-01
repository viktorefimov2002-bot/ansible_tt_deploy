import type { AdminState } from "../state/useAdmin";
import { Button } from "./ui/button";

export function NotificationsView({ admin }: { admin: AdminState }) {
  const result = admin.notifications;
  const writable = admin.principal?.role === "admin";
  return (
    <section className="panel">
      <h2>Operational and security events</h2>
      <p>Updated every 30 seconds. Read status belongs to your account.</p>
      <label className="check">
        <input
          type="checkbox"
          checked={admin.unreadOnly}
          onChange={(event) => admin.filterNotifications(event.target.checked)}
        />
        Unread only
      </label>
      {result && (
        <p role="status">{result.unread_count} unread notifications</p>
      )}
      {admin.notificationsLoading && (
        <p role="status">Loading notifications…</p>
      )}
      {admin.notificationError && <p role="alert">{admin.notificationError}</p>}
      {!admin.notificationsLoading &&
        !admin.notificationError &&
        result?.items.length === 0 && (
          <p>
            {admin.unreadOnly
              ? "No unread notifications."
              : "No notifications yet."}
          </p>
        )}
      {result?.items.map((entry) => (
        <article
          className={`notification ${entry.read_at ? "" : "notification-unread"}`}
          key={entry.id}
        >
          <div className="section-heading">
            <h3>{entry.title}</h3>
            <span className={`badge severity-${entry.severity}`}>
              {entry.severity}
            </span>
          </div>
          <p>{entry.message}</p>
          <p>
            <time dateTime={entry.created_at}>
              {new Date(entry.created_at).toLocaleString()}
            </time>{" "}
            · <strong>{entry.read_at ? "Read" : "Unread"}</strong>
          </p>
          {(entry.target_id || entry.request_id) && (
            <details>
              <summary>Event context</summary>
              <dl>
                <dt>Target</dt>
                <dd>
                  {entry.target_type} {entry.target_id || "—"}
                </dd>
                <dt>Request</dt>
                <dd>{entry.request_id || "—"}</dd>
                {entry.read_at && (
                  <>
                    <dt>Read at</dt>
                    <dd>{new Date(entry.read_at).toLocaleString()}</dd>
                  </>
                )}
              </dl>
            </details>
          )}
          <div className="actions">
            {entry.target_type === "job" && entry.target_id && (
              <Button
                variant="outline"
                onClick={() => {
                  admin.navigate("Jobs");
                  admin.selectJob(entry.target_id!);
                }}
              >
                Inspect job
              </Button>
            )}
            {writable && (
              <Button
                variant="outline"
                disabled={admin.notificationBusy}
                onClick={() =>
                  void admin.markNotification(entry.id, !entry.read_at)
                }
              >
                {entry.read_at ? "Mark unread" : "Mark read"}
              </Button>
            )}
          </div>
        </article>
      ))}
      {result && (
        <p>
          {result.total
            ? `${result.offset + 1}–${result.offset + result.items.length} of ${result.total} notifications`
            : "0 notifications"}
        </p>
      )}
      <div className="actions">
        <Button
          variant="outline"
          disabled={
            admin.notificationOffset === 0 ||
            admin.notificationsLoading ||
            admin.notificationBusy
          }
          onClick={() =>
            admin.setNotificationOffset(
              Math.max(0, admin.notificationOffset - 50),
            )
          }
        >
          Previous notifications
        </Button>
        <Button
          variant="outline"
          disabled={
            !result ||
            admin.notificationOffset + result.items.length >= result.total ||
            admin.notificationsLoading ||
            admin.notificationBusy
          }
          onClick={() =>
            admin.setNotificationOffset(admin.notificationOffset + 50)
          }
        >
          Older notifications
        </Button>
      </div>
    </section>
  );
}
