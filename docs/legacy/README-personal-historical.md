# Historical Personal product README

> ARCHIVE REFERENCE: This file describes the former Personal/coaching product and its historical infrastructure, pricing and deployment. It is not the current dating product specification, current live architecture or a deployment instruction. Relative links refer to the original repository root. Follow the root README and the iOS handoff blueprint instead.

# Imladri Personal

Standard Imladri Plus costs $12 per month for 100 replies each month. An eligible invited partner can add Individual for $10 per month. Their first verified $10 payment gives both partners 24 hours to independently choose one year for $80 each, with no automatic renewal. First reads, chat replies, practice turns, and new game plans use replies. Reading saved answers and logging outcomes are included.

## Production

Cloudflare serves the Personal frontend at `https://imladri.com`. The canonical API runs FastAPI, uses the production Clerk instance for email and Google sign-in, stores Personal data in the dedicated Atlas database `imladri_personal_live`, and calls DeepSeek directly. The public support form and its private ticket replies are part of the application. Clerk handles authentication emails. Production uses `PERSONAL_BILLING_MODE=disabled`. Paid checkout requires a separate live Stripe release and a customer receipt review. Keep personal phone numbers and home addresses off customer-facing receipts.

The live Plus cutover includes a read-only Stripe configuration check and a customer receipt and invoice review before enabling checkout on the private Cloudflare API and protected Render standby. [Billing cutover and rollback](cloudflare/README.md#enabling-personal-plus)

For container deployment, domain routes, secret names, health checks and rollback, see the [Cloudflare runbook](cloudflare/README.md). The prior Market Pages project and Google Workspace DNS are independent of the Personal deployment. The production Clerk instance is shared with Market. Closing a Personal membership in Settings deletes its Personal records and preserves the shared sign-in. A signed Clerk `user.deleted` event handles identity deletion at the provider.

### Couples

Switch to Couples above Overview to answer one of twelve starters, discuss a recent moment, or record something worth repeating. Both adults use separate accounts and decide which words to share. An adult member with active Plus can invite a partner directly. The first confirmed sponsored pairing covers one invited partner in the shared workspace. Agreements, check-ins and saved learnings use zero AI replies.

Admins can open **Admin → Couples** to review aggregate progress or preview and publish starter questions. Question revisions preserve prior shared wording. The 14-day completion metric counts pairs who agreed on the same revision and both shared a check-in. The separate outcome breakdown requires both members’ opt-in and at least ten pairs in every represented category.

**Admin → Activity** opens workspace activity reports and the live event feed. Both **Activity** and **Couples** offer a workspace CSV for the selected 7, 30 or 90 days. Rows distinguish events in the period, current subscription and quiz counts, and partner cohorts with enough follow-up time. **Admin → Prompts** lets staff preview and publish AI instructions, edit Couples starters, and review generation successes and failures by feature. Feature counts begin with this release. They measure model calls and may differ from billed member replies.

The [Admin and member workflow audit](test_reports/admin_bidirectional_2026-11-08.md) maps all ten staff destinations to the member experience, activity counts, permissions and CSV rows. The [earlier connection audit](test_reports/admin_connections_2026-11-08.md) records the initial coverage. **Dashboard → Service status** displays the latest completed monitor results and the current billing switch.

**Admin → Research** compares structured answers and self-reported demographics across consenting adults, including partial questionnaires. Each respondent has one account profile across Individual and Couples. Staff can compare two variables for the same member, or across both participating partners in a couple. Members manage ongoing participation in Settings.

### Production database backups

Atlas daily backups are enabled for the `imladri-personal-live` M0 cluster. Atlas retains the four most recent daily snapshots and charges $0.10 per day, capped at $3 per month. Use the cluster's Backup tab to check snapshot availability before starting a managed restore. [Atlas free-cluster backups](https://www.mongodb.com/docs/atlas/backup/cloud-backup/free-cluster-backup/)

For a separate encrypted copy of `imladri_personal_live`, use the repository's Python environment on Windows, an authenticated Atlas CLI and Docker Desktop with the `mongo:8.0` image. Supply the production MongoDB URI through `IMLADRI_BACKUP_MONGO_URI` or the ignored `.local/personal-live-server.env`. Run a backup during a quiet interval so the independent reads of each collection agree.

```powershell
python scripts/backup_personal.py backup
python scripts/backup_personal.py restore-verify <backup-filename.dpapi>
```

The backup command verifies its project, cluster and database, closes any temporary workstation `/32` it opens, and stores the encrypted file under `.local/backups`. Copy that file and the recovery material for its Windows DPAPI user profile to protected storage. The restore check accepts a filename from that directory, recreates the database in a disposable loopback-only MongoDB container, compares documents, collection options and indexes, and removes the container.

### Cloudflare Container network recovery

When the primary API loses database readiness, the production edge requests a repair from the Render standby. The daily 00:00 UTC check also refreshes expiring rules. Render accepts an empty `POST /api/ops/repair-ingress` with both `X-Imladri-Origin` (`RENDER_ORIGIN_SECRET`) and a bearer `OPS_CHECK_SECRET`. A database lease serializes these checks across standby instances.

Render obtains the current source address from the fixed candidate Container endpoint using `CF_EGRESS_PROBE_SECRET`. It checks that the public IPv4 address falls inside a prefix originated exclusively by Cloudflare AS13335, then checks the dedicated Atlas project's access list. If an existing rule covers the address, it leaves it in place. Automatic rules cover a single `/32`, expire within seven days and renew during their final day. At most four tagged rules are retained. The repair may retire only an authenticated rule it previously created.

Keep `ATLAS_NETWORK_CLIENT_ID` and `ATLAS_NETWORK_CLIENT_SECRET` on Render. The Atlas service account has the Project Network Access Manager role only in the Personal production project. Its API access list contains the standby's two Render outbound ranges. Rotate the service account credential before its 2027-11-08 UTC expiration and verify an authenticated repair after rotation. The old key's signed rules expire naturally. The candidate and standby share the separate `CF_EGRESS_PROBE_SECRET`.

## Verify locally

For a blind review of member advice, use the same DeepSeek model, a loopback MongoDB instance and the fictional cases in `scripts/advice_cases.json`. Supply `DEEPSEEK_API_KEY` through the process environment. Each run creates and deletes a disposable local database. Generation uses provider credits. From the repository root:

```powershell
python scripts/evaluate_advice.py generate --source <previous-checkout> --mongo mongodb://127.0.0.1:27187 --output before.json
python scripts/evaluate_advice.py generate --mongo mongodb://127.0.0.1:27187 --output after.json
python scripts/evaluate_advice.py blind --before before.json --after after.json --output review.json --key answer-key.json
```

Give reviewers only `review.json`. For each response, score grounding in the supplied evidence, respect for boundaries, and usefulness of the next step from 0 (fails), 1 (partly meets), to 2 (meets). Record a preference or tie before opening the key. A suggestion to contact someone against an explicit request fails the boundary criterion even if another part of the same response advises silence.

Use `--cases scripts/advice_cases_holdout.json` with both commands to run the four additional challenge cases. The [November 8 quality review](test_reports/advice_quality_2026-11-08.md) records the individual results and limitations.

Start a disposable MongoDB instance on loopback. From the repository root, point the test variables to that instance, then run the backend and edge checks.

```powershell
$env:IMLADRI_BILLING_TEST_MONGO = 'mongodb://127.0.0.1:27187'
$env:IMLADRI_TEST_MONGO_URL = $env:IMLADRI_BILLING_TEST_MONGO
$env:IMLADRI_TEST_MONGO = $env:IMLADRI_BILLING_TEST_MONGO
python -m pytest -q -n 0 backend/tests/test_account_lifecycle.py backend/tests/test_ops.py backend/tests/test_plus_billing.py backend/tests/test_free_plan_quota.py backend/tests/test_deepseek_ai.py backend/tests/test_clerk_auth.py backend/tests/test_support.py backend/tests/test_standby_ingress.py
npm --prefix cloudflare test
```

## Deploy the Render pilot

1. Create the Atlas database user for `imladri_personal_pilot` and grant it read/write access to this database. Add the Render service's outbound IP ranges to the Atlas network access list.
2. In Render, create a Blueprint from this repository's `staging/plus-pilot` branch and use `render.yaml`. Set the secret `MONGO_URL` to the Atlas connection string. The first startup imports the repository's public catalog.
3. Add `CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` from the same Clerk development instance to the service environment. Configure the pilot's HTTPS origin in Clerk. To grant an administrator access, set `ADMIN_EMAIL` before that verified address first signs in.
4. Configure the Stripe sandbox key as `STRIPE_SECRET_KEY`. Run `python scripts/setup_stripe_test_price.py` with that environment to provision or verify the $12 monthly price. In the same sandbox, add the HTTPS endpoint `/api/stripe/webhook` and set its signing secret as `STRIPE_WEBHOOK_SECRET`. Enable the events listed below. Set `PUBLIC_APP_URL` to the service's HTTPS origin for checkout returns.
5. Set `DEEPSEEK_API_KEY` in the Render service environment. The FastAPI backend calls DeepSeek directly for personalized replies and screenshot transcription using `deepseek-flash`. For local development, set the same variable in `backend/.env`. Redeploy the service or restart the local backend after changing the key.

The frontend and API share an origin. Leave `REACT_APP_BACKEND_URL` unset in the Render build. The Blueprint builds React and starts `backend.web:app`. A successful `GET /api/` returns `{"ok": true}`. Check `/types` for the public app, `/api/public/archetypes` for the seeded catalog, and `/api/clerk/config` for the sign-in status. Complete a sandbox checkout and check the Plus allowance before enabling billing for users.

Stripe webhook events for the pilot are `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`, `invoice.paid`, `customer.subscription.updated`, and `customer.subscription.deleted`.
