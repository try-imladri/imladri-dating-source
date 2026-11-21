# Imladri dating source archive

This reviewed source snapshot preserves the November 19 dating implementation, including uncommitted work, and the Personal code it was built on. It is a **reuse reference**, not a release-certified iOS app or instructions to restore the old website.

Public read access was authorized by the owner on November 21, 2026. The source and linked iOS blueprint can be read or cloned without a GitHub token. Public visibility does not grant write access to this repository; changes to the owner's branches still require authorized access.

## Start here

- Read the [current iOS blueprint and repository map](https://github.com/try-imladri/imladri-ios-blueprint/blob/main/README.md) and its [agent instructions](https://github.com/try-imladri/imladri-ios-blueprint/blob/main/AGENTS.md) first. Those current decisions take precedence over historical product, marketing, billing and deployment references in this archive.
- Read `PROVENANCE.md` for the snapshot boundary and exclusions.
- Read the [current reuse map](https://github.com/try-imladri/imladri-ios-blueprint/blob/main/REUSE_MAP.md) before choosing modules.
- Dating backend: `backend/dating/` (FastAPI routes, schemas, domain service, catalog).
- Dating web UI: `frontend/src/pages/dating/`, `frontend/src/components/dating/`, `frontend/src/lib/dating.js`.
- Dating admin: `frontend/src/pages/admin/DatingAdmin.jsx`.
- Dating regression tests: `backend/tests/test_dating.py`, `test_dating_host.py`, `test_dating_review.py`.
- Legacy assessment and Your Type catalogs are available for selective review; private coaching and Couples data must never become dating profile data automatically.

## Existing behavior and limits

The dating implementation includes adult opt-in, explicit profile data, reciprocal preferences, photo review, discovery, mutual matches, messaging, block/report, explicit answer-sharing and revocation, feedback, deletion/export, and admin review. Source presence does not establish that every feature is production-ready.

Matching currently uses deterministic eligibility and preference scoring. It makes no dating LLM calls and is not validated as a chemistry predictor. Discovery needs adaptation to the final limited weekly introduction model. Photo review is not identity verification. Old Personal billing and coaching entitlements are not the final dating membership.

## Local verification and deployment

Review the backend/frontend package manifests and synthetic tests before running anything. No credentials, live database copies or real customer fixtures are supplied. Use isolated local services and fresh development configuration. Some historical container build files refer to excluded binary vendor payloads; patch source, build recipes and manifests are retained, but restoring that legacy container is outside this archive's reuse purpose. Do not deploy old domain routes, API origins or authentication settings without a separate reviewed migration.

The former product's README is retained in `docs/legacy/README-personal-historical.md` solely as historical reference. The live waitlist and owner admin are a separate Cloudflare project.
