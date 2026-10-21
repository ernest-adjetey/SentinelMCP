# Deployment Assets

This directory contains checked-in Prometheus and Grafana configuration for local development.
It is intentionally limited to components SentinelMCP currently implements.

Start a metrics-enabled SentinelMCP gateway on host port `8000`, then run:

```bash
docker compose up -d postgres prometheus grafana
```

Prometheus scrapes `http://host.docker.internal:8000/metrics` every 15 seconds. Open Prometheus at
`http://localhost:9090` and the provisioned SentinelMCP dashboard at `http://localhost:3000`.
Anonymous Grafana access is enabled only for this local stack. Do not copy that authentication
setting into a shared deployment.

The dashboard shows bounded aggregate signals: gateway and execution outcomes, request rate,
latency percentiles, audit persistence failures, idempotency dispositions, adapter attempts, and
process-local circuit states. It cannot reconstruct payloads or identities because those values
are intentionally absent from metric labels.
