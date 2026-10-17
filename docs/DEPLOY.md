# Deploying VendorLink to Google Cloud

**Not needed to run or test locally:** for that, see `docs/LOCAL_SETUP.md`, which also explains
how to get the Gemini, Slack and Gmail keys. This page covers only what a Cloud Run deploy adds.

For Cloud Run, `infra/deploy.sh` creates:
- the KMS keyrings (`vl-gateway`, `vl-vendor`, `vl-retailer`);
- the two GCS buckets;
- the Model Armor template `vl-screen`.

It also sets the matching environment on each service. You provide the project, the APIs, the
secrets and the Cloud SQL database.

## 1. Project and APIs

```bash
gcloud auth login
gcloud config set project <PROJECT_ID>
gcloud auth application-default login        # lets local tools use your account
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  firestore.googleapis.com cloudkms.googleapis.com storage.googleapis.com modelarmor.googleapis.com \
  aiplatform.googleapis.com secretmanager.googleapis.com sqladmin.googleapis.com \
  cloudscheduler.googleapis.com cloudtrace.googleapis.com
gcloud firestore databases create --location=asia-south1
```

Create the service accounts and roles listed in `infra/iam.md`.

## 2. Secrets (Secret Manager, never env files)

Create each secret once. Add a new version to change one.

```bash
put() { printf '%s' "$2" | gcloud secrets create "$1" --data-file=- 2>/dev/null \
        || printf '%s' "$2" | gcloud secrets versions add "$1" --data-file=-; }
put vl-admin-token    "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
put vl-operator-token "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
put vl-flash-model    "<FLASH_MODEL>"
put vl-pro-model      "<PRO_MODEL>"
put vl-live-model     "<LIVE_MODEL>"
```

- **Models on Vertex AI:** deployed services use Vertex AI (`GOOGLE_GENAI_USE_VERTEXAI=true`, set by
  `deploy.sh`), not the API key. Check the names exist on Vertex AI in your region; they can
  differ from the AI Studio list.
- **The deployed console's operator token:** get it with
  `gcloud secrets versions access latest --secret vl-operator-token`.

## 3. Durable jobs: Cloud SQL Postgres

```bash
gcloud sql instances create vl-jobs --database-version=POSTGRES_16 --edition=enterprise \
  --region=asia-south1 --tier=db-f1-micro
gcloud sql databases create vl_jobs --instance=vl-jobs
gcloud sql users create vl --instance=vl-jobs --password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
```

Store the connection string (Unix socket form, which Cloud Run mounts):

```bash
put vl-checkpoint-dsn "postgresql://vl:<PASSWORD>@/vl_jobs?host=/cloudsql/<PROJECT_ID>:asia-south1:vl-jobs"
```

## 4. Slack and Gmail when deployed

Cloud Run reaches MCP servers over **HTTP**, not `npx`.

- **Slack:** run `@zencoderai/slack-mcp-server` as its own service with `--transport http` and an
  `AUTH_TOKEN`. Then:
  ```bash
  put vl-mcp-slack-headers '{"Authorization": "Bearer <AUTH_TOKEN>"}'
  export MCP_SLACK_URL=https://<slack-mcp-service>/mcp       # read by deploy.sh
  ```
- **Gmail:** this server has no HTTP mode. Leave Gmail off when deployed (`MCP_GMAIL_URL` empty);
  approvals and escalations still go through Slack. Create the header secret anyway, because
  `deploy.sh` mounts it:
  ```bash
  put vl-mcp-gmail-headers '{}'
  ```

## 5. Deploy

```bash
export GOOGLE_CLOUD_PROJECT=<PROJECT_ID> TAG=$(git rev-parse --short HEAD)   # needs one commit
export CLOUDSQL_INSTANCE=<PROJECT_ID>:asia-south1:vl-jobs
gcloud builds submit --config infra/cloudbuild.yaml --substitutions=_TAG=$TAG
./infra/deploy.sh
./infra/deploy_web.sh            # web app on Firebase Hosting (firebase login first)
```

If the web app is not on `https://<PROJECT_ID>.web.app`, set `WEB_ORIGIN` before `deploy.sh`.
It is the only browser origin the APIs accept, and the base of links in Slack and email messages.

## Cloud variable reference

| Variable | Where it comes from |
|---|---|
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | your project; region `asia-south1` |
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` (set by `deploy.sh`); models via Vertex AI, not the API key |
| `GCS_BUCKET`, `KMS_KEYRING`, `MODEL_ARMOR_TEMPLATE` | created and set by `deploy.sh` |
| `VL_ADMIN_TOKEN`, `VL_OPERATOR_TOKEN`, model names | Secret Manager (section 2) |
| `VL_CHECKPOINT_DSN` | Secret Manager (section 3) |
| `MCP_SLACK_URL`, `vl-mcp-*-headers` | section 4 |
| `VL_CORS_ORIGINS`, `VL_PUBLIC_URL` | set by `deploy.sh` from `WEB_ORIGIN` |
