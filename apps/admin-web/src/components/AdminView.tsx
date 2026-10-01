import { useState } from "react";
import type { AdminState } from "../state/useAdmin";
import { canCancel, invitationStatus, type Page } from "../domain/types";
import { Button } from "./ui/button";
import { ConfirmAction } from "./ConfirmAction";
import { ServerForm } from "./ServerForm";
import { UserDetails, UserForm } from "./UserDetails";
import { AuditView } from "./AuditView";
import { NotificationsView } from "./NotificationsView";
import { MonitoringView } from "./MonitoringView";

const pages: Page[] = [
  "Dashboard",
  "Servers",
  "VPN users",
  "Invitations",
  "Jobs",
  "Monitoring",
  "Audit",
  "Notifications",
];
export function AdminView({ admin }: { admin: AdminState }) {
  const writable = admin.principal?.role === "admin";
  const [copied, setCopied] = useState(false);
  if (admin.restoring)
    return (
      <main className="login">
        <p role="status">Restoring session…</p>
      </main>
    );
  if (!admin.principal)
    return (
      <main className="login panel">
        <p className="eyebrow">TRUSTTUNNEL · ADMINISTRATION</p>
        <h1>Sign in</h1>
        <p>Use your administrator or viewer account and authenticator.</p>
        {admin.error && <p role="alert">{admin.error}</p>}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const form = event.currentTarget;
            const data = new FormData(form);
            void admin.login(
              String(data.get("username")),
              String(data.get("password")),
              String(data.get("otp")),
            );
            (form.elements.namedItem("password") as HTMLInputElement).value =
              "";
            (form.elements.namedItem("otp") as HTMLInputElement).value = "";
          }}
        >
          <fieldset disabled={admin.busy}>
            <label>
              Username
              <input
                name="username"
                required
                maxLength={128}
                autoComplete="username"
              />
            </label>
            <label>
              Password
              <input
                name="password"
                type="password"
                required
                maxLength={256}
                autoComplete="current-password"
              />
            </label>
            <label>
              Authenticator code
              <input
                name="otp"
                required
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                autoComplete="one-time-code"
              />
            </label>
            <Button type="submit">
              {admin.busy ? "Signing in…" : "Sign in"}
            </Button>
          </fieldset>
        </form>
      </main>
    );
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          TrustTunnel<span>Administration</span>
        </div>
        <nav aria-label="Main navigation">
          {pages.map((page) => (
            <Button
              variant="outline"
              key={page}
              aria-current={admin.page === page ? "page" : undefined}
              onClick={() => admin.navigate(page)}
            >
              {page}
              {page === "Notifications" &&
                !!admin.notifications?.unread_count && (
                  <span
                    className="notification-count"
                    aria-label={`${admin.notifications.unread_count} unread`}
                  >
                    {admin.notifications.unread_count}
                  </span>
                )}
            </Button>
          ))}
        </nav>
        <p className="identity">
          {admin.principal.username}
          <br />
          <strong>{writable ? "Administrator" : "Viewer · read-only"}</strong>
        </p>
        <Button
          variant="outline"
          disabled={admin.busy}
          onClick={() => void admin.logout()}
        >
          Sign out
        </Button>
      </aside>
      <main id="main">
        <header>
          <div>
            <p className="eyebrow">CONTROL PLANE</p>
            <h1>{admin.page}</h1>
          </div>
          <Button
            variant="outline"
            disabled={
              admin.busy ||
              admin.loading ||
              (admin.page === "Monitoring" && admin.monitoringLoading) ||
              (admin.page === "Audit" && admin.auditLoading) ||
              (admin.page === "Notifications" && admin.notificationsLoading)
            }
            onClick={admin.refresh}
          >
            Refresh
          </Button>
        </header>
        {!writable && (
          <p className="banner">
            Read-only access. You can inspect resources and job logs.
          </p>
        )}
        {admin.error && (
          <p role="alert" className="error">
            {admin.error}
          </p>
        )}
        {admin.notice && (
          <p role="status" className="banner">
            {admin.notice}
          </p>
        )}
        {admin.loading && <p role="status">Loading latest state…</p>}
        {admin.page === "Monitoring" && <MonitoringView admin={admin} />}
        {admin.page === "Audit" && <AuditView admin={admin} />}
        {admin.page === "Notifications" && <NotificationsView admin={admin} />}
        {admin.page === "Dashboard" && (
          <>
            <div className="stats">
              <section className="panel">
                <h2>Servers</h2>
                <strong>{admin.servers.length}</strong>
                <p>
                  {admin.servers.filter((node) => node.enabled).length} enabled
                </p>
                <Button
                  variant="outline"
                  onClick={() => admin.navigate("Servers")}
                >
                  Manage servers
                </Button>
              </section>
              <section className="panel">
                <h2>VPN users</h2>
                <strong>{admin.users.length}</strong>
                <p>
                  {
                    admin.users.filter(
                      (person) =>
                        person.enabled &&
                        (!person.expires_at ||
                          Date.parse(person.expires_at) > Date.now()),
                    ).length
                  }{" "}
                  with valid access
                </p>
                <Button
                  variant="outline"
                  onClick={() => admin.navigate("VPN users")}
                >
                  View users
                </Button>
              </section>
              <section className="panel">
                <h2>Recent jobs</h2>
                <strong>
                  {
                    admin.jobs.filter((job) =>
                      ["queued", "running"].includes(job.status),
                    ).length
                  }
                </strong>
                <p>In progress in the latest 50 jobs</p>
                <Button
                  variant="outline"
                  onClick={() => {
                    admin.setJobOffset(0);
                    admin.navigate("Jobs");
                  }}
                >
                  View jobs
                </Button>
              </section>
            </div>
            <section className="panel">
              <h2>Server status</h2>
              <p>
                Status comes from the last completed diagnostic; use server
                status checks to update it.
              </p>
              {!admin.servers.length && (
                <p>
                  No managed servers yet. Add an already bootstrapped server to
                  begin.
                </p>
              )}
              {admin.servers.map((node) => (
                <p key={node.id}>
                  <strong>{node.name}</strong> ·{" "}
                  {node.enabled ? node.status : "Disabled"} · Last seen{" "}
                  {node.last_seen_at
                    ? new Date(node.last_seen_at).toLocaleString()
                    : "Never"}
                </p>
              ))}
            </section>
          </>
        )}
        {admin.page === "Servers" && (
          <>
            {writable && (
              <section className="panel">
                <ServerForm
                  busy={admin.busy}
                  onSave={admin.actions.saveServer}
                />
              </section>
            )}
            {!admin.servers.length && !admin.loading && (
              <p>No managed servers yet.</p>
            )}
            {admin.servers.map((node) => (
              <section className="panel" key={node.id}>
                <div className="section-heading">
                  <h2>{node.name}</h2>
                  <span className="badge">
                    {node.enabled ? node.status : "Disabled"}
                  </span>
                </div>
                <p>
                  {node.domain} · {node.location || "Location unspecified"}
                </p>
                <dl>
                  <dt>SSH host</dt>
                  <dd>
                    {node.ssh_user}@{node.hostname}:{node.ssh_port}
                  </dd>
                  <dt>Public IP</dt>
                  <dd>{node.public_ip}</dd>
                  <dt>Management key</dt>
                  <dd>{node.ssh_configured ? "Configured" : "Missing"}</dd>
                  <dt>Pinned host fingerprint</dt>
                  <dd>{node.host_key_fingerprint || "Not configured"}</dd>
                  <dt>Last seen</dt>
                  <dd>
                    {node.last_seen_at
                      ? new Date(node.last_seen_at).toLocaleString()
                      : "Never"}
                  </dd>
                </dl>
                {writable && (
                  <>
                    <div className="actions">
                      <Button
                        disabled={admin.busy || !node.enabled}
                        onClick={() =>
                          void admin.actions.diagnostic(node.id, "preflight")
                        }
                      >
                        Run pre-flight
                      </Button>
                      <Button
                        variant="outline"
                        disabled={admin.busy || !node.enabled}
                        onClick={() =>
                          void admin.actions.diagnostic(node.id, "status")
                        }
                      >
                        Check status
                      </Button>
                      {node.enabled ? (
                        <ConfirmAction
                          label="Disable server"
                          description="Clients will no longer be able to provision or retrieve configurations on this server. This does not uninstall the node."
                          busy={admin.busy}
                          onConfirm={() =>
                            void admin.actions.serverEnabled(node)
                          }
                        />
                      ) : (
                        <Button
                          disabled={admin.busy}
                          onClick={() => void admin.actions.serverEnabled(node)}
                        >
                          Enable server
                        </Button>
                      )}
                    </div>
                    <ServerForm
                      key={`${node.id}-${node.name}-${node.hostname}`}
                      node={node}
                      busy={admin.busy}
                      onSave={admin.actions.saveServer}
                    />
                    <details>
                      <summary>Replace management SSH identity</summary>
                      <p>
                        Verify the replacement host key through a trusted
                        channel. Server jobs must be idle.
                      </p>
                      <form
                        onSubmit={(event) => {
                          event.preventDefault();
                          const data = new FormData(event.currentTarget);
                          void admin.actions.rotateSSH(
                            node.id,
                            String(data.get("host")),
                            String(data.get("key")),
                          );
                          event.currentTarget.reset();
                        }}
                      >
                        <fieldset disabled={admin.busy}>
                          <label>
                            Replacement host public key
                            <input name="host" required autoComplete="off" />
                          </label>
                          <label>
                            Replacement private key
                            <textarea
                              name="key"
                              required
                              autoComplete="off"
                              spellCheck={false}
                            />
                          </label>
                          <Button type="submit">Replace SSH identity</Button>
                        </fieldset>
                      </form>
                    </details>
                  </>
                )}
              </section>
            ))}
          </>
        )}
        {(admin.page === "VPN users" || admin.page === "Invitations") && (
          <>
            <section className="panel">
              {admin.page === "VPN users" && writable && (
                <details>
                  <summary>Add VPN user</summary>
                  <UserForm busy={admin.busy} onSave={admin.actions.saveUser} />
                </details>
              )}
              <label>
                VPN user
                <select
                  aria-label="VPN user"
                  value={admin.userId}
                  onChange={(event) => {
                    admin.selectUser(event.target.value);
                    setCopied(false);
                  }}
                >
                  <option value="">Select a user</option>
                  {admin.users.map((person) => (
                    <option key={person.id} value={person.id}>
                      {person.display_name}
                      {!person.enabled && " (disabled)"}
                    </option>
                  ))}
                </select>
              </label>
              {!admin.users.length && !admin.loading && (
                <p>No VPN users yet.</p>
              )}
            </section>
            {admin.page === "VPN users" && admin.user && (
              <UserDetails key={admin.user.id} admin={admin} />
            )}
            {admin.page === "Invitations" && admin.user && (
              <section className="panel">
                <h2>Invitations for {admin.user.display_name}</h2>
                {writable && (
                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      const data = new FormData(event.currentTarget);
                      setCopied(false);
                      void admin.issueInvitation(Number(data.get("lifetime")));
                    }}
                  >
                    <fieldset
                      disabled={
                        admin.busy ||
                        !admin.user.enabled ||
                        (!!admin.user.expires_at &&
                          Date.parse(admin.user.expires_at) <= Date.now())
                      }
                    >
                      <label>
                        Invitation lifetime
                        <select name="lifetime" defaultValue="86400">
                          <option value="3600">1 hour</option>
                          <option value="86400">1 day</option>
                          <option value="604800">7 days</option>
                        </select>
                      </label>
                      <Button type="submit">Create invitation</Button>
                    </fieldset>
                  </form>
                )}
                {admin.invite && writable && (
                  <div className="banner">
                    <label>
                      Private invitation link
                      <input
                        readOnly
                        value={admin.invite}
                        autoComplete="off"
                        onFocus={(event) => event.currentTarget.select()}
                      />
                    </label>
                    <p>Shown only once. Share privately with this VPN user.</p>
                    <div className="actions">
                      <Button
                        onClick={() => {
                          void navigator.clipboard
                            .writeText(admin.invite)
                            .then(() => setCopied(true))
                            .catch(() => setCopied(false));
                        }}
                      >
                        Copy link
                      </Button>
                      <Button variant="outline" onClick={admin.dismissInvite}>
                        Dismiss link
                      </Button>
                    </div>
                    {copied && <p role="status">Copied.</p>}
                  </div>
                )}
                {admin.detailsLoading && (
                  <p role="status">Loading invitations…</p>
                )}
                {!admin.detailsLoading && !admin.invitations.length && (
                  <p>No invitations yet.</p>
                )}
                {admin.invitations.map((invitation) => (
                  <article className="device" key={invitation.id}>
                    <strong>{invitationStatus(invitation)}</strong>
                    <p>
                      Expires {new Date(invitation.expires_at).toLocaleString()}
                    </p>
                    {writable &&
                      invitationStatus(invitation) === "Available" && (
                        <ConfirmAction
                          label="Revoke invitation"
                          description="The unused invitation link will stop working."
                          busy={admin.busy}
                          onConfirm={() =>
                            void admin.actions.revokeInvite(
                              admin.userId,
                              invitation.id,
                            )
                          }
                        />
                      )}
                  </article>
                ))}
              </section>
            )}
          </>
        )}
        {admin.page === "Jobs" && (
          <>
            <section className="panel">
              <h2>Operations</h2>
              <p>
                Jobs {admin.jobOffset + 1}–{admin.jobOffset + admin.jobs.length}
                . Updated every 5 seconds.
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Operation</th>
                      <th>Status</th>
                      <th>Progress</th>
                      <th>Created</th>
                      <th>Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {admin.jobs.map((job) => (
                      <tr key={job.id}>
                        <td>{job.type}</td>
                        <td>{job.status}</td>
                        <td>{job.progress}%</td>
                        <td>{new Date(job.created_at).toLocaleString()}</td>
                        <td>
                          <Button
                            variant="outline"
                            onClick={() => admin.selectJob(job.id)}
                          >
                            Inspect {job.id.slice(0, 8)}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!admin.jobs.length && !admin.loading && (
                <p>No jobs on this page.</p>
              )}
              <div className="actions">
                <Button
                  variant="outline"
                  disabled={admin.jobOffset === 0 || admin.loading}
                  onClick={() =>
                    admin.setJobOffset(Math.max(0, admin.jobOffset - 50))
                  }
                >
                  Previous jobs
                </Button>
                <Button
                  variant="outline"
                  disabled={admin.jobs.length < 50 || admin.loading}
                  onClick={() => admin.setJobOffset(admin.jobOffset + 50)}
                >
                  Older jobs
                </Button>
              </div>
            </section>
            {admin.job && (
              <section className="panel">
                <h2>{admin.job.type}</h2>
                <p>Job {admin.job.id}</p>
                <p>
                  <strong>{admin.job.status}</strong> · {admin.job.progress}% ·
                  Attempts: {admin.job.attempts}
                </p>
                {admin.job.cancel_requested_at && (
                  <p role="status">
                    Cancellation requested; waiting for a safe checkpoint.
                  </p>
                )}
                {admin.job.error_message && (
                  <p role="alert">{admin.job.error_message}</p>
                )}
                {writable && canCancel(admin.job) && (
                  <ConfirmAction
                    label="Cancel job"
                    description="Queued jobs stop immediately. Running jobs stop at the next safe checkpoint."
                    busy={admin.busy}
                    onConfirm={() => void admin.actions.cancel(admin.job!.id)}
                  />
                )}
                {admin.job.result?.checks && (
                  <>
                    <h3>Diagnostic checks</h3>
                    <p>{admin.job.result.ready ? "Ready" : "Not ready"}</p>
                    <dl>
                      {Object.entries(admin.job.result.checks).map(
                        ([name, state]) => (
                          <div key={name}>
                            <dt>{name.replaceAll("_", " ")}</dt>
                            <dd>{state}</dd>
                          </div>
                        ),
                      )}
                    </dl>
                  </>
                )}
                <h3>Live job logs</h3>
                {admin.logError && <p role="alert">{admin.logError}</p>}
                <ol className="logs" aria-label="Job logs">
                  {admin.logs.map((entry) => (
                    <li key={entry.sequence}>
                      <time>{new Date(entry.at).toLocaleTimeString()}</time>{" "}
                      {entry.message}
                    </li>
                  ))}
                </ol>
                {!admin.logs.length && <p>No retained logs yet.</p>}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
