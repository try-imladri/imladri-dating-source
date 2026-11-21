export const researchTools = [
  ["comparisons", "Questionnaire comparisons"], ["consent", "Consent"], ["your-type", "Your Type"],
  ["tone", "Conversation tone"], ["exports", "Record exports"], ["evidence", "Assessment evidence"],
  ["prospective", "Prospective comparisons"], ["independent", "Independent review"],
  ["rehearsal", "Rehearsal review"], ["migration", "Database & history"],
];

export const researchToolPages = {
  consent: "researchConsent", "your-type": "researchYourType", tone: "researchTone", exports: "researchExports",
  evidence: "researchEvidence", prospective: "researchProspective", independent: "researchIndependent",
  rehearsal: "researchRehearsal", migration: "researchMigration",
};

export function researchTool(value) {
  const key = (value || "").replace(/^#/, "");
  const aliases = { "your-type-research": "your-type", "research-record-exports": "exports",
    "database-migration": "migration", "main-consent": "consent", "tone-research": "tone" };
  const result = aliases[key] || key;
  return researchTools.some(([id]) => id === result) ? result : "comparisons";
}
