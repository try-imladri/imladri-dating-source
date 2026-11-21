// Explicit fictional receipts for navigation tests; never imported by app code.
import { MAIN_CONSENT_PERMISSIONS } from "@/lib/mainConsent";
const nodeCrypto = require("crypto");
export const consentDigest = value => nodeCrypto.createHash("sha256").update(value).digest("hex");
export const consentSubtle = { digest: async (_, bytes) => Uint8Array.from(nodeCrypto.createHash("sha256").update(bytes).digest()).buffer };
export const consentScopes = ["questionnaire", "your_type", "tone", "member_csv", "couple_csv", "staff_history", "trends", "couples_statistics", "couples_return"];
export function installConsentCrypto() {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: { randomUUID: nodeCrypto.randomUUID, subtle: consentSubtle } });
  globalThis.TextEncoder = require("util").TextEncoder;
  return () => previous ? Object.defineProperty(globalThis, "crypto", previous) : delete globalThis.crypto;
}
export function mainConsentFixture(owner, options = {}) {
  const { reviewed = true, pairId = null, sharingReviewed = reviewed, permissions = {}, eligibility = {}, pairEnabled = false } = options;
  const notice = "Fictional canonical notice: save unfinished answers and profile details, use relevant context for requested personalized advice, and independently choose research, staff access and current-partner sharing.";
  const pairNotice = "Share selected current and future personality or intimacy assessments only with this fictional current partner until sharing is withdrawn. Your Type and private notes are excluded.";
  return { expected_member: owner, notice_version: "main-consent-v1", notice: notice, notice_sha256: consentDigest(notice),
    revision: reviewed ? 2 : 0, reviewed, welcome_complete: reviewed, reviewed_at: reviewed ? "2026-11-16T00:00:00Z" : null, pair_id: pairId,
    scopes: Object.fromEntries(consentScopes.map(scope => [scope, { eligible: eligibility[scope] ?? (scope === "couple_csv" ? !!pairId : true),
      unavailable_reason: "This fictional purpose is not available in the current context.", purpose: "Fictional isolated-test purpose: " + scope,
      permission: { version: MAIN_CONSENT_PERMISSIONS[scope], revision: 3, enabled: permissions[scope] === true,
        state_sha256: consentDigest(scope + ":" + String(permissions[scope] === true)) } }])),
    pair_sharing: { pair_id: pairId, available: !!pairId, eligible: !!pairId, reviewed: !!pairId && sharingReviewed,
      partner_display_name: "your fictional current partner", notice_version: "couples-assessment-sharing-v1", notice: pairNotice,
      notice_sha256: consentDigest(pairNotice), expected_revision: pairId && sharingReviewed ? 2 : 0,
      personality: pairEnabled, intimacy: pairEnabled, state_sha256: pairId ? consentDigest(pairId + ":" + sharingReviewed + ":" + pairEnabled) : null,
      revocable: !!pairId && pairEnabled, revocation_revision: pairId && sharingReviewed ? 2 : 0,
      unavailable_reason: pairId ? null : "No eligible current pair needs a sharing decision." } };
}
export function applyMainConsent(before, body) {
  const result = JSON.parse(JSON.stringify(before));
  result.reviewed = true; result.welcome_complete = true; result.revision += 1; result.reviewed_at = "2026-11-16T01:00:00Z";
  for (const scope of consentScopes) {
    const old = before.scopes[scope].permission, enabled = body.choices[scope].enabled;
    result.scopes[scope].permission = { ...old, enabled, revision: old.revision + Number(enabled !== old.enabled),
      state_sha256: enabled === old.enabled ? old.state_sha256 : consentDigest(old.state_sha256 + ":" + enabled) };
  }
  return result;
}
