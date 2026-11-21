# PostgreSQL datastore cutover

This release prepares the existing Render PostgreSQL instance to replace the
application databases in Atlas. Installing the code does not change the active
datastore. Cloudflare hosting, domains, authentication, payments and email
providers remain in place.

## Application connections

`IMLADRI_DATABASE_BACKEND` explicitly selects `mongodb`, `postgresql` or
`render_gateway`. There is no automatic fallback between datastores.

Render uses a dedicated PostgreSQL login with a fixed namespace. The preview,
Personal production, Market API and Market worker have distinct credentials;
only the two Market logins share their application's namespace. Runtime logins
have DML permissions with forced row security and cannot create schemas, roles,
temporary objects or alter the namespace map. Runtime startup verifies the
schema and privileges without running owner DDL.

Cloudflare uses the authenticated HTTPS repository on the existing Personal
Render standby. It receives a dedicated signing secret and the existing origin
secret, never PostgreSQL credentials or a Render management token. The gateway
uses a fixed operation/collection vocabulary, bounded requests and admission,
fresh signatures, and durable UUID/body deduplication in the mutation's SQL
transaction. Retries do not repeat a committed debit. Private document images
are not cached as replay results; a lost committed image returns an explicit
uncertain-result response.

`imladri_app` retains authoritative BSON alongside queryable JSONB. Native SQL
transactions serialize collection mutations and enforce unique tokens. The
document query/update adapters implement the reviewed application vocabulary;
unsupported operators fail rather than silently approximating results.

## Maintenance and copying

`IMLADRI_DATABASE_MAINTENANCE=paused` stops ordinary API traffic and startup
writers. `/api/ready` remains available and explicitly reports maintenance.
The legacy scheduler worker must park before database access. A provider
environment edit alone does not prove the running old process is fenced.

The fixed `/api/internal/database-migration` endpoint is available only on the
exact paused production standby, behind both the origin secret and a separate
operator bearer secret. Requests bind the exact deployed Git SHA. Its fixed
child module strips unrelated credentials, hardens capabilities/NoNewPrivs
before networking, and is bounded in duration, output and concurrency.
Shutdown cancels and reaps pending children. A durable owner-only operation
journal distinguishes completed, running and uncertain outcomes; uncertain
operations are not automatically repeated or reset.

The controller must independently verify provider identities, target storage
capacity and the complete writer/background/DDL fence. Its reviewed source,
role and provider configuration is hashed. The full safe fence JSON is also
installed in trusted environment configuration: request timestamps, capacity
hash and payload budget must exactly match that trusted proof.

Fixed operator commands inspect metadata, discover only authorized database
names, prepare the reviewed runtime roles, copy Personal, copy Market, archive
legacy databases, and verify a completed copy through the runtime logins.
Discovery through scoped source credentials is not proof that an entire Atlas
account has no other databases.

Active source copies use an acknowledged majority metadata barrier after all
application writers stop, then one pinned replica-set snapshot. Native MongoDB
TTL may continue outside that immutable view; it is not claimed to be stopped.
Repeated source and target manifests check BSON identities, types, values,
empty collections and index definitions. Unclassified active collections,
unsupported options or missing erasure/history protections block the copy.
Market coordinate types are checked on the same pinned view before target
writes; Decimal128 trigonometry is not silently converted to float.

Existing source credentials write the barrier in their already-authorized
database. A separately authorized read-only Recovery source can use the fixed
`imladri_migration_control` database on that same cluster instead. Neither path
implicitly grants additional Atlas roles.

Inactive historical data goes to owner-only `imladri_archive`, with raw BSON,
raw collection/index metadata, canonical extended JSON and immutable receipts.
This does not infer research consent or give application logins archive access.

## Release gates

1. Build the reviewed Git archive with canonical file bytes; inventory the
   actual application virtual environment, native wheels and OS distribution.
   Preserve raw vendor findings and unresolved historical/shared-source cases.
2. Deploy the preview before production. Verify the exact provider release,
   startup/runtime fingerprint, role permissions and maintenance behavior.
3. Inspect every known active and historical Atlas database, including Recovery.
   Verify target capacity against current provider metrics within the approved
   existing plan; do not infer free disk space from database size alone.
4. Fence every actual source API, worker, scheduler and application background
   writer. Verify the current processes/controllers, then make the final pinned
   copies and check their complete receipts through scoped runtime logins.
5. Remove owner/source/operator/Atlas-repair credentials before reopening
   PostgreSQL traffic. The datastore factory rejects these credentials when
   PostgreSQL is active outside maintenance.
6. Verify Individual, Couples, billing state, research/history, deletion/export,
   admin, background work and cold restart against the copied data. Keep the
   owner's excluded live payment/sign-in/email/Atlas-drill work excluded.
7. Route all consumers to PostgreSQL, disable obsolete Atlas network-repair
   schedules, and remove stale shadow private payloads after preserving safe
   hash/count evidence. Confirm no live consumer still needs MongoDB.

Before new PostgreSQL traffic, an unchanged MongoDB source may support an
explicit rollback. After PostgreSQL accepts writes, recovery must preserve its
current state and use a PostgreSQL-capable release/standby. Sending traffic back
to an older MongoDB snapshot would lose those writes and is not a valid
rollback. MongoDB account deactivation is gated on all copies and consumers,
including older databases, being verified; code readiness alone is insufficient.
