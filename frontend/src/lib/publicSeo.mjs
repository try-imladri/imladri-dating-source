import { publicSite, publicBrandImage, publicBrandImageAlt } from "./publicCatalogMetadata.mjs";

// One public metadata source for the initial edge response and client navigation.
export const publicPages = {
  "/": ["Imladri | Meet Your Type. Understand Your Fit.", "Join Imladri’s founding community for intentional dating built around attraction, relationship preferences, and what you choose to share. Adults 18+."],
  "/learn": ["Learn About Attraction & Relationships | Imladri", "Explore communication archetypes and editorial public-persona interpretations with clear limits, then learn how Imladri’s intentional dating beta works."],
  "/types": ["Personality Archetypes | Imladri", "Explore Imladri's personality archetypes, communication patterns, strengths and relationship dynamics across both editions."],
  "/figures": ["Public Figures & Personality Archetypes | Imladri", "Search Imladri's public figures and explore career examples behind their archetype interpretations, strengths and communication styles."],
  "/plans": ["Free Dating Founding Beta | Imladri", "Join Imladri’s free dating founding beta. No card required for enrollment. Introductions depend on local participation and reciprocal preferences."],
  "/about": ["About Imladri | Intentional Dating", "Learn why Imladri is building an intentional dating community around attraction, relationship intentions, preferences, and personal choice."],
  "/how-it-works": ["How Imladri Dating Works", "Learn about dating enrollment, profile choices, optional personality and intimacy preferences, mutual interest, and the founding community."],
  "/support": ["Contact Imladri Support", "Get help with your Imladri account, membership, billing or personal data through our private support request system."],
  "/privacy": ["Privacy Policy | Imladri", "Read how Imladri handles personal information, questionnaire answers, conversations, research permissions and data controls."],
  "/terms": ["Terms of Service | Imladri", "Read the terms for using Imladri's personality insights, conversation tools, Individual and Couples workspaces and memberships."],
  "/billing": ["Billing, Messages & Refund Policy | Imladri", "Understand Imladri's subscriptions, shared message allowances, refills, cancellation and refund policy before purchasing."],
};

export const articleRoute = /^\/(types|figures)\/([a-z0-9]+(?:-[a-z0-9]+)*)\/?$/;
export const normalizedPath = path => path.replace(/\/$/, "") || "/";
export const isPublicStaticPage = path => Object.hasOwn(publicPages, normalizedPath(path));
export const isIndexablePath = path => isPublicStaticPage(path) || articleRoute.test(path);
export const isPrivatePage = path => /^\/(?:app|admin|dating|legacy|sign-in|sign-up)(?:\/|$)/.test(path)
  || /^\/(?:login|signup|waitlist|upgrade|couples\/invite)\/?$/.test(path);

export function staticPageMetadata(path) {
  const clean = normalizedPath(path);
  const entry = publicPages[clean];
  return entry ? {type: "website", title: entry[0], description: entry[1],
    canonical: publicSite + clean, image: publicSite + publicBrandImage, alt: publicBrandImageAlt} : null;
}

export function pageStructuredData(meta) {
  return {"@context": "https://schema.org", "@graph": [
    {"@type": "Organization", "@id": publicSite + "/#organization", name: "Imladri", url: publicSite + "/"},
    {"@type": "WebSite", "@id": publicSite + "/#website", name: "Imladri", url: publicSite + "/",
      publisher: {"@id": publicSite + "/#organization"}},
    {"@type": "WebPage", "@id": meta.canonical + "#webpage", url: meta.canonical,
      name: meta.title, description: meta.description, inLanguage: "en",
      isPartOf: {"@id": publicSite + "/#website"}},
  ]};
}
