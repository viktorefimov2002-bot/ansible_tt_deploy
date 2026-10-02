import type { ServerConfiguration } from "./types";

export const defaultConfiguration: ServerConfiguration = {
  ipv6_available: true,
  allow_private_network_connections: false,
  tls_handshake_timeout_secs: 10,
  client_listener_timeout_secs: 600,
  connection_establishment_timeout_secs: 30,
  tcp_connections_timeout_secs: 604800,
  udp_connections_timeout_secs: 300,
};
export const timeoutFields = [
  ["tls_handshake_timeout_secs", "TLS handshake timeout", 120],
  ["client_listener_timeout_secs", "Client listener timeout", 86400],
  [
    "connection_establishment_timeout_secs",
    "Connection establishment timeout",
    600,
  ],
  ["tcp_connections_timeout_secs", "TCP connection timeout", 2592000],
  ["udp_connections_timeout_secs", "UDP connection timeout", 86400],
] as const;
export function configurationState(state: string) {
  return (
    {
      idle: "No configuration changes",
      validated: "Validated · ready to apply",
      apply_pending: "Apply queued or running",
      applied: "Applied successfully",
      rolled_back: "Apply failed · previous configuration restored",
      rollback_failed: "Apply and rollback failed · inspect the server",
      failed: "Apply failed",
      unknown: "Outcome unconfirmed · inspect the server",
      cancelled: "Apply cancelled",
    }[state] || state
  );
}
