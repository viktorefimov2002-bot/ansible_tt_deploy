import { useState, type ChangeEvent } from "react";
import type { AuditFilters } from "../domain/types";
import type { AdminState } from "../state/useAdmin";
import { Button } from "./ui/button";

function localDateTime(value: string | undefined) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

export function AuditView({ admin }: { admin: AdminState }) {
  const result = admin.audit;
  const [draft, setDraft] = useState<AuditFilters>(() => ({
    ...admin.auditFilters,
    since: localDateTime(admin.auditFilters.since),
    until: localDateTime(admin.auditFilters.until),
  }));
  const field = (name: keyof AuditFilters) => ({
    value: draft[name] || "",
    onChange: (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setDraft((values) => ({ ...values, [name]: event.target.value })),
  });
  return (
    <section className="panel">
      <h2>Audit log</h2>
      <p>
        Administrative and security activity, newest first. Updated every 30
        seconds.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const filters: AuditFilters = {};
          for (const [key, value] of data.entries()) {
            const input = String(value).trim();
            if (input)
              filters[key as keyof AuditFilters] =
                key === "since" || key === "until"
                  ? new Date(input).toISOString()
                  : input;
          }
          admin.applyAuditFilters(filters);
        }}
      >
        <fieldset>
          <div className="filter-grid">
            <label>
              Action
              <input
                name="action"
                {...field("action")}
                maxLength={128}
                placeholder="e.g. server.create"
              />
            </label>
            <label>
              Result
              <input
                name="result"
                {...field("result")}
                maxLength={32}
                placeholder="e.g. success"
              />
            </label>
            <label>
              Actor type
              <select name="actor_type" {...field("actor_type")}>
                <option value="">All actors</option>
                <option value="admin">Administrator</option>
                <option value="vpn_user">VPN user</option>
                <option value="system">System</option>
              </select>
            </label>
            <label>
              Actor ID
              <input name="actor_id" {...field("actor_id")} maxLength={128} />
            </label>
            <label>
              From (local time)
              <input name="since" {...field("since")} type="datetime-local" />
            </label>
            <label>
              Until (local time)
              <input name="until" {...field("until")} type="datetime-local" />
            </label>
          </div>
          <details>
            <summary>Filter by target or request</summary>
            <div className="filter-grid">
              <label>
                Target type
                <input
                  name="target_type"
                  {...field("target_type")}
                  maxLength={64}
                />
              </label>
              <label>
                Target ID
                <input
                  name="target_id"
                  {...field("target_id")}
                  maxLength={128}
                />
              </label>
              <label>
                Request ID
                <input
                  name="request_id"
                  {...field("request_id")}
                  maxLength={128}
                />
              </label>
            </div>
          </details>
          <div className="actions">
            <Button type="submit" disabled={admin.auditLoading}>
              Apply filters
            </Button>
            <Button
              variant="outline"
              type="button"
              onClick={() => {
                setDraft({});
                admin.applyAuditFilters({});
              }}
            >
              Clear filters
            </Button>
          </div>
        </fieldset>
      </form>
      {admin.auditLoading && <p role="status">Loading audit events…</p>}
      {admin.auditError && <p role="alert">{admin.auditError}</p>}
      {!admin.auditLoading &&
        !admin.auditError &&
        result?.items.length === 0 && (
          <p>No audit events match these filters.</p>
        )}
      {!!result?.items.length && (
        <div className="table-wrap" tabIndex={0} aria-label="Audit events">
          <table className="audit-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Action / result</th>
                <th>Actor</th>
                <th>Target</th>
                <th>Request</th>
              </tr>
            </thead>
            <tbody>
              {result.items.map((entry) => (
                <tr key={entry.id}>
                  <td>
                    <time dateTime={entry.created_at}>
                      {new Date(entry.created_at).toLocaleString()}
                    </time>
                  </td>
                  <td>
                    <strong>{entry.action}</strong>
                    <br />
                    {entry.result}
                  </td>
                  <td>
                    {entry.actor_type}
                    <br />
                    <span className="identifier">{entry.actor_id || "—"}</span>
                  </td>
                  <td>
                    {entry.target_type}
                    <br />
                    <span className="identifier">{entry.target_id || "—"}</span>
                  </td>
                  <td>
                    <span className="identifier">
                      {entry.request_id || "—"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {result && (
        <p>
          {result.total
            ? `${result.offset + 1}–${result.offset + result.items.length} of ${result.total} audit events`
            : "0 audit events"}
        </p>
      )}
      <div className="actions">
        <Button
          variant="outline"
          disabled={admin.auditOffset === 0 || admin.auditLoading}
          onClick={() =>
            admin.setAuditOffset(Math.max(0, admin.auditOffset - 50))
          }
        >
          Previous audit events
        </Button>
        <Button
          variant="outline"
          disabled={
            !result ||
            admin.auditOffset + result.items.length >= result.total ||
            admin.auditLoading
          }
          onClick={() => admin.setAuditOffset(admin.auditOffset + 50)}
        >
          Older audit events
        </Button>
      </div>
    </section>
  );
}
