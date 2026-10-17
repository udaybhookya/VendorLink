# IAM and tenancy (MVP)

| Service account | Runs | May use |
|---|---|---|
| `vl-gateway` | trust-gateway | Firestore (`gw.*` collections), KMS keyring `vl-gateway` (sign), Model Armor |
| `vl-vendor-tenant` | vendor-svc | Firestore (`vendor.*`), KMS keyring `vl-vendor` (sign), GCS bucket `vl-vendor-*`, Vertex AI |
| `vl-retailer-tenant` | retailer-svc | Firestore (`retailer.*`), KMS keyring `vl-retailer` (sign), GCS bucket `vl-retailer-*`, Vertex AI |
| `vl-mocks` | mocks | nothing |
| `vl-slack-mcp` | slack-mcp | secrets `vl-slack-bot-token`, `vl-slack-team-id`, `vl-slack-mcp-auth` |
| `vl-scheduler` | Cloud Scheduler | `roles/run.invoker` |

`infra/bootstrap.sh` creates these accounts and every binding on this page (ADR 0014).

- Vendor and retailer tenants have **separate keyrings and buckets**; neither SA can use the
  other's keys. In the MVP they share one GCP project: **say so openly**. Production: one project
  per tenant.
- Secrets (`vl-admin-token`, model names) live in Secret Manager and are mounted as env vars.
- `VL_DEV_ENDPOINTS=false` in every deployed service. The hosted demo sets `VL_DEMO_ENDPOINTS=true`:
  `/dev/reset|tick|tamper` then answer only the admin token (tamper also the operator token), and
  the dev outbox stays closed (ADR 0014).
- All tenant services also get `roles/cloudtrace.agent`; vendor and retailer get `roles/aiplatform.user`.
- Cloud adapters (ADR 0005), each tested against a fake client:
  - KMS: `roles/cloudkms.signerVerifier` **and** `roles/cloudkms.admin` scoped to the tenant's own
    keyring (keys are created on first use; rotation adds a version and disables the old one).
    Keyrings: `vl-gateway`, `vl-vendor`, `vl-retailer` in `$REGION`.
  - GCS: `roles/storage.objectAdmin` on the tenant's own bucket only (`vl-vendor-<project>`,
    `vl-retailer-<project>`); uniform access, public access prevented.
  - Model Armor: `vl-gateway` needs `roles/modelarmor.user`; template `vl-screen`
    (prompt injection / jailbreak, malicious URLs). If Model Armor is unreachable the gateway
    reports `screening:unavailable` and blocks (fails closed).
  - Firestore: `roles/datastore.user`.

## Human in the loop (ADR 0004)
- `vl-retailer-tenant` also needs `roles/cloudsql.client` (LangGraph checkpoints in Cloud SQL
  Postgres) and access to secrets `vl-checkpoint-dsn`, `vl-mcp-slack-headers`, `vl-mcp-gmail-headers`.
- `vl-vendor-tenant` needs `vl-mcp-gmail-headers` (nudges to vendors with an email on file).
- `vl-scheduler` (Cloud Scheduler OIDC) may invoke `retailer-svc` only.
- MCP servers run as their own services (or a vendor's hosted MCP endpoint); their OAuth tokens
  live in Secret Manager and reach us only as request headers.
- The scheduler sends `"for_date": "tomorrow"`; the job resolves it in the retailer's timezone.
