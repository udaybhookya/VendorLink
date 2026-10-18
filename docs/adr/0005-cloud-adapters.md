# ADR 0005 — Cloud adapters: GCS, Cloud KMS, Model Armor

Status: accepted · 16 Oct 2026

## Context
Firestore was already real. The other three GCP backends raised `NotImplementedError`, so
`VL_BLOB=gcs`, `VL_SIGNER=kms` and `VL_SCREEN=model_armor` could not be deployed. Each stays
behind its `vl_core` interface with a local fallback (CLAUDE.md rule 8). The client libraries are
in the `gcp` extra and imported lazily; tests drive every adapter through a fake client
(`tests/unit/test_cloud_adapters.py`).

## Decisions
1. **`GcsBlobStore`**: one bucket per tenant; URIs are `gs://<bucket>/<key>`. `get` refuses URIs
   for any other bucket, and keys pass the same `..`/absolute-path check as local blobs. The sync
   client runs in `asyncio.to_thread`.
2. **`KmsSigner`**: one `EC_SIGN_ED25519` crypto key per key id in the tenant's keyring, created
   on first use. Key ids are mapped to valid KMS ids (`kms_key_id`, hashed when not
   representable). The private key never leaves KMS; signatures verify with the existing
   `verify_signature`.
   - **Rotation without renaming:** KMS keys cannot be renamed, so the `x#next` staging id is
     *a new version of x's key*, and `promote` disables the older versions. The active version
     is the **oldest enabled** one, so a restart between staging and promoting keeps signing with
     the old key until the gateway has accepted the new one.
   - `Signer.promote` became `async` (its only caller is `HostControl.rotate`).
3. **`ModelArmorScreener`**: `sanitize_user_prompt` against a template; any filter whose result
   is `MATCH_FOUND` becomes a `model_armor:<filter>` finding. It runs **in addition to** the
   heuristic patterns, which also cover our Telugu and Bahasa phrasings. It **fails closed**:
   an unreachable Model Armor yields `screening:unavailable`, and the gateway refuses the relay
   (503 `screening_unavailable`) without revoking the sender: an outage is not an attack. A
   relayed body is screened as one text (its `*_b64` binary fields are left to `/screen`), in
   10,000-character parts for Model Armor. `Screener.scan_text` became `async` so the gateway's event loop never
   blocks on the network. Text extraction and image checks stay local. The Liaison's answer check
   (`screen_text`) stays heuristic and synchronous: answers are short and parsed
   deterministically anyway.
4. `infra/deploy.sh` now creates the keyrings, buckets and Model Armor template idempotently and
   passes `KMS_KEYRING`, `GCS_BUCKET`, `MODEL_ARMOR_TEMPLATE`, project and location to each
   service. Roles are listed in `infra/iam.md`.

## Consequences
- Failing closed means a Model Armor outage stops cross-tenant relays. That is the intended
  trade-off for a gateway that denies by default; the event shows up in Observability → Safety.
- KMS keys cannot be deleted, so dev resets against a real project leave old keys behind.
  Use a separate project or keyring for experiments.
