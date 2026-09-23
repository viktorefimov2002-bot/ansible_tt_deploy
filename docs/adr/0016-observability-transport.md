# ADR-0016: Managed-node metrics transport

- Status: Proposed; implemented for TTCP-011, live-node acceptance pending
- Date: 2026-09-23
- Basis: architecture section 24, TTCP-011 request, ADR-0015

## Decision

Keep TTCP-010's distribution `prometheus-node-exporter` on `127.0.0.1:9100` and
keep SSH forwarding disabled for `ttcp`. A separate, read-only Control Plane
collector reads enabled server records and their encrypted management identities
from PostgreSQL. For each configured `ttcp` node, it uses OpenSSH with the stored
host-key pin and a fixed remote Python command to read node_exporter over node
loopback. The same SSH invocation runs TTCP-010's no-argument, root-owned
status helper through its exact sudoers rule. The helper reads only fixed
systemd properties. When the service has a main process, it asks that running
executable for its documented `--version` with a two-second timeout; only a
strict numeric version is exposed. The management identity is materialized
only in a private temporary directory during the scrape. No SSH forwarding,
generic sudo, additional node agent, public exporter port, or node firewall
change is required.

The endpoint's `--version` behavior is documented in the upstream
[command-line reference](https://github.com/TrustTunnel/TrustTunnel/blob/master/CONFIGURATION.md#command-line-arguments).

VictoriaMetrics scrapes the collector every 30 seconds on a private Compose
network. Samples carry the immutable Control Plane `server_id`; a separate
`ttcp_server_info` gauge carries current name, enabled flag, and Control Plane
status from PostgreSQL. `ttcp_node_scrape_success` distinguishes unavailable
node metrics from a healthy collector. `up{job="ttcp-managed-nodes"}` distinguishes
collector failure. Control Plane status still means management reachability and
does not become VPN service health. Scraping never writes server state or jobs.

Service state, process presence, automatic restart count, and available running
version are separate metrics. `NRestarts` counts systemd automatic restarts,
not operator-initiated restarts. Missing or invalid helper output sets
`ttcp_service_probe_success` to zero without discarding host metrics.

The collector selects only the node metric families needed for this MVP and
bounds each response to 2 MiB and 10,000 selected samples. SSH has a 5-second
connection timeout and collection is bounded to 12 seconds per node. The existing
seven-day retention and 384 MiB VictoriaMetrics container limit remain. The
collector has a 128 MiB container limit. Failure removes current metrics on
subsequent scrapes; it cannot stop VPN traffic or modify PostgreSQL records.

## Consequences

The collector needs database read access to server records and the same
encryption key already used by API/worker. That makes the collector a trusted
Control Plane component. Its port is only on the internal metrics network; it is
not published on the host. A future deployment with more nodes may need a
dedicated read-only database role or a different transport. For 2–4 nodes,
short-lived pinned SSH scrapes avoid a new PKI and per-node network endpoint.

An existing server registered under an SSH user other than `ttcp`, without a
key, or without a pinned host key reports scrape failure. The operator should
complete TTCP-010 onboarding and register the matching identity. A live managed
node and Linux Docker host are needed to validate the full SSH transport.
Nodes bootstrapped before this helper extension need an idempotent TTCP-010
rebootstrap with the same public key before the service metrics are available.
