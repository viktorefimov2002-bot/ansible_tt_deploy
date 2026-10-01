export type Page =
  | "Dashboard"
  | "Servers"
  | "VPN users"
  | "Invitations"
  | "Jobs";
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
