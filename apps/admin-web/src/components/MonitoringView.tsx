import type {
  Metrics,
  MonitoredServer,
  MonitoringWindow,
} from "../domain/types";
import type { AdminState } from "../state/useAdmin";

const percent = (value: number | null) =>
  value === null ? "Unavailable" : `${value.toFixed(1)}%`;
const state = (value: boolean | null) =>
  value === null ? "Unknown" : value ? "Running" : "Stopped";
function bandwidth(value: number | null) {
  if (value === null) return "Unavailable";
  if (value >= 1048576) return `${(value / 1048576).toFixed(1)} MiB/s`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB/s`;
  return `${value.toFixed(0)} B/s`;
}
function Trend({
  server,
  metric,
  label,
}: {
  server: MonitoredServer;
  metric: keyof Pick<Metrics, "cpu_percent" | "memory_percent">;
  label: string;
}) {
  const samples = server.recent;
  const available = samples.filter((sample) => sample[metric] !== null);
  const segments: string[][] = [[]];
  samples.forEach((sample, index) => {
    const value = sample[metric];
    if (value === null) {
      if (segments.at(-1)?.length) segments.push([]);
    } else {
      segments
        .at(-1)!
        .push(
          `${8 + (index / Math.max(1, samples.length - 1)) * 304},${92 - Math.max(0, Math.min(100, value)) * 0.8}`,
        );
    }
  });
  return (
    <div className="trend">
      <h4>{label}</h4>
      {!available.length ? (
        <p>No recent samples.</p>
      ) : (
        <>
          <svg
            viewBox="0 0 320 100"
            role="img"
            aria-label={`${label} trend for ${server.name}; ${available.length} samples; latest ${percent(available.at(-1)![metric])}`}
          >
            <title>
              {label} over the selected period (0–100%). Gaps indicate missing
              data.
            </title>
            <line x1="8" y1="92" x2="312" y2="92" className="trend-axis" />
            <line x1="8" y1="12" x2="312" y2="12" className="trend-axis" />
            {segments
              .filter((segment) => segment.length)
              .map((segment, index) => {
                if (segment.length === 1) {
                  const [x, y] = segment[0].split(",");
                  return (
                    <circle
                      key={index}
                      cx={x}
                      cy={y}
                      r="3"
                      className="trend-point"
                    />
                  );
                }
                return (
                  <polyline
                    key={index}
                    points={segment.join(" ")}
                    className="trend-line"
                  />
                );
              })}
          </svg>
          <p>Latest sampled: {percent(available.at(-1)![metric])}</p>
        </>
      )}
    </div>
  );
}

export function MonitoringView({ admin }: { admin: AdminState }) {
  const data = admin.monitoring;
  return (
    <>
      <section className="panel">
        <h2>System health</h2>
        <p>
          Updated every 30 seconds. Missing or stale measurements are shown as
          unavailable.
        </p>
        <label className="window-select">
          Recent period
          <select
            value={admin.monitoringWindow}
            onChange={(event) =>
              admin.setMonitoringWindow(event.target.value as MonitoringWindow)
            }
          >
            <option value="1h">Last hour</option>
            <option value="6h">Last 6 hours</option>
            <option value="24h">Last 24 hours</option>
          </select>
        </label>
        {admin.monitoringLoading && <p role="status">Loading monitoring…</p>}
        {admin.monitoringError && <p role="alert">{admin.monitoringError}</p>}
        {admin.monitoringError && data && (
          <p className="banner">
            Monitoring could not be refreshed. Showing the last successful
            observation from {new Date(data.generated_at).toLocaleString()}.
          </p>
        )}
        {data && (
          <>
            <p>
              <strong>Overall health: {data.system.status}</strong> · Checked{" "}
              <time dateTime={data.generated_at}>
                {new Date(data.generated_at).toLocaleString()}
              </time>
            </p>
            <dl className="metrics-grid">
              <div>
                <dt>Database</dt>
                <dd>{data.system.postgres}</dd>
              </div>
              <div>
                <dt>Job coordination</dt>
                <dd>{data.system.redis}</dd>
              </div>
              <div>
                <dt>Metrics storage</dt>
                <dd>{data.system.victoriametrics}</dd>
              </div>
              <div>
                <dt>Health collector</dt>
                <dd>{data.system.collector}</dd>
              </div>
            </dl>
            <p>
              {
                data.servers.filter((server) => server.health === "healthy")
                  .length
              }{" "}
              healthy ·{" "}
              {
                data.servers.filter((server) =>
                  ["degraded", "unavailable"].includes(server.health),
                ).length
              }{" "}
              need attention ·{" "}
              {
                data.servers.filter((server) => server.health === "unknown")
                  .length
              }{" "}
              unknown ·{" "}
              {
                data.servers.filter((server) => server.health === "disabled")
                  .length
              }{" "}
              disabled
            </p>
            {!data.servers.length && <p>No managed servers to monitor.</p>}
          </>
        )}
      </section>
      {data?.servers.map((server) => (
        <section className="panel" key={server.id}>
          <div className="section-heading">
            <h2>{server.name}</h2>
            <span className={`badge health-${server.health}`}>
              {server.health}
            </span>
          </div>
          <p>
            Last metrics observation:{" "}
            {server.observed_at
              ? new Date(server.observed_at).toLocaleString()
              : "None"}
          </p>
          <dl className="metrics-grid">
            <div>
              <dt>CPU usage</dt>
              <dd>{percent(server.metrics.cpu_percent)}</dd>
            </div>
            <div>
              <dt>Memory usage</dt>
              <dd>{percent(server.metrics.memory_percent)}</dd>
            </div>
            <div>
              <dt>Disk usage</dt>
              <dd>{percent(server.metrics.disk_percent)}</dd>
            </div>
            <div>
              <dt>Load (1 minute)</dt>
              <dd>
                {server.metrics.load1 === null
                  ? "Unavailable"
                  : server.metrics.load1.toFixed(2)}
              </dd>
            </div>
            <div>
              <dt>Network received</dt>
              <dd>
                {bandwidth(server.metrics.network_receive_bytes_per_second)}
              </dd>
            </div>
            <div>
              <dt>Network sent</dt>
              <dd>
                {bandwidth(server.metrics.network_transmit_bytes_per_second)}
              </dd>
            </div>
          </dl>
          <div className="trends">
            <Trend
              server={server}
              metric="cpu_percent"
              label="Recent CPU usage"
            />
            <Trend
              server={server}
              metric="memory_percent"
              label="Recent memory usage"
            />
          </div>
          <p>
            Samples every {data.step_seconds} seconds; gaps indicate missing
            data.
          </p>
          <dl className="metrics-grid">
            <div>
              <dt>VPN service</dt>
              <dd>{state(server.service.active)}</dd>
            </div>
            <div>
              <dt>VPN process</dt>
              <dd>{state(server.service.process_running)}</dd>
            </div>
            <div>
              <dt>Service observation</dt>
              <dd>{server.service.probe}</dd>
            </div>
            <div>
              <dt>Automatic restarts</dt>
              <dd>{server.service.automatic_restarts ?? "Unknown"}</dd>
            </div>
          </dl>
          <details>
            <summary>Management diagnostic</summary>
            <p>
              {server.management_status} · Last seen{" "}
              {server.last_seen_at
                ? new Date(server.last_seen_at).toLocaleString()
                : "Never"}
            </p>
          </details>
        </section>
      ))}
    </>
  );
}
