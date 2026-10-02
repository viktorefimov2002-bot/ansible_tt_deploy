export type Page =
  | "Dashboard"
  | "Servers"
  | "VPN users"
  | "Invitations"
  | "Jobs"
  | "Monitoring"
  | "Audit"
  | "Notifications";
export interface Principal {
  id: string;
  username: string;
  role: "admin" | "viewer";
  session_id: string;
}
export interface ServerInput {
  name: string;
  hostname: string;
  public_ip: string;
  domain: string;
  location: string | null;
  ssh_user: string;
  ssh_port: number;
  acme_http: boolean;
}
export interface Server extends ServerInput {
  id: string;
  enabled: boolean;
  status: string;
  last_seen_at: string | null;
  ssh_configured: boolean;
  host_key_fingerprint: string | null;
  lifecycle_state: string;
  lifecycle_job_id: string | null;
  preflight_passed_at: string | null;
  trusttunnel_version: string | null;
  desired_trusttunnel_version: string | null;
  config_revision_id: string | null;
  config_state: string;
  config_job_id: string | null;
}
export type LifecycleAction = "deploy" | "update" | "restart" | "uninstall";
export interface ServerConfiguration {
  ipv6_available: boolean;
  allow_private_network_connections: boolean;
  tls_handshake_timeout_secs: number;
  client_listener_timeout_secs: number;
  connection_establishment_timeout_secs: number;
  tcp_connections_timeout_secs: number;
  udp_connections_timeout_secs: number;
}
export interface ConfigRevision {
  id: string;
  server_id: string;
  revision: number;
  config: ServerConfiguration | null;
  validation_valid?: boolean;
  created_by: string | null;
  created_at: string;
  applied_at: string | null;
  status: string;
  job_id: string | null;
  failure_code: string | null;
  failure_message: string | null;
}
export interface UserInput {
  display_name: string;
  device_limit: number;
  expires_at: string | null;
}
export interface VpnUser extends UserInput {
  id: string;
  enabled: boolean;
  access_mode: "selected" | "all";
  server_ids: string[];
}
export interface Device {
  id: string;
  name: string;
  platform: string | null;
  enabled: boolean;
}
export interface Credential {
  id: string;
  device_id: string;
  server_id: string;
  username: string;
  status: string;
  applied_at: string | null;
}
export interface Invitation {
  id: string;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
}
export interface LogEvent {
  sequence: number;
  at: string;
  message: string;
  code: string;
}
export interface Job {
  id: string;
  type: string;
  target_id: string | null;
  target_type: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  attempts: number;
  created_at: string;
  error_message: string | null;
  cancellable: boolean;
  cancel_requested_at: string | null;
  history: LogEvent[];
  result: {
    outcome?: string;
    ready?: boolean;
    checks?: Record<string, string>;
    config_apply?: {
      revision_id: string;
      state: "applied" | "rolled_back" | "rollback_failed";
      active: boolean;
    };
  };
}
export const terminal = (job: Job) =>
  ["succeeded", "failed", "cancelled"].includes(job.status);
export const canCancel = (job: Job) =>
  !terminal(job) &&
  !job.cancel_requested_at &&
  (job.status === "queued" || job.cancellable);
export function invitationStatus(invitation: Invitation) {
  if (invitation.revoked_at) return "Revoked";
  if (invitation.used_at) return "Used";
  return Date.parse(invitation.expires_at) <= Date.now()
    ? "Expired"
    : "Available";
}

export interface AuditEvent {
  id: string;
  created_at: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  request_id: string | null;
  result: string;
}
export interface AuditFilters {
  action?: string;
  actor_type?: string;
  actor_id?: string;
  target_type?: string;
  target_id?: string;
  result?: string;
  since?: string;
  until?: string;
  request_id?: string;
}
export interface ListPage<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}
export interface Notification {
  id: string;
  created_at: string;
  severity: "info" | "warning" | "critical";
  title: string;
  message: string;
  target_type: string;
  target_id: string | null;
  request_id: string | null;
  read_at: string | null;
}
export interface NotificationPage extends ListPage<Notification> {
  unread_count: number;
}
export type MonitoringWindow = "1h" | "6h" | "24h";
export type DependencyHealth = "healthy" | "unavailable";
export interface Metrics {
  cpu_percent: number | null;
  memory_percent: number | null;
  load1: number | null;
  disk_percent: number | null;
  network_receive_bytes_per_second: number | null;
  network_transmit_bytes_per_second: number | null;
}
export interface MonitoredServer {
  id: string;
  name: string;
  enabled: boolean;
  management_status: string;
  last_seen_at: string | null;
  health: "healthy" | "degraded" | "unavailable" | "unknown" | "disabled";
  observed_at: string | null;
  metrics: Metrics;
  service: {
    probe: DependencyHealth | "unknown";
    active: boolean | null;
    process_running: boolean | null;
    automatic_restarts: number | null;
  };
  recent: (Metrics & { at: string })[];
}
export interface Monitoring {
  generated_at: string;
  window: MonitoringWindow;
  step_seconds: number;
  system: {
    status: "healthy" | "degraded";
    postgres: DependencyHealth;
    redis: DependencyHealth;
    victoriametrics: DependencyHealth;
    collector: DependencyHealth | "unknown";
  };
  servers: MonitoredServer[];
}
