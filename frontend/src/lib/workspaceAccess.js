const PERSONAL_SECTIONS = new Set(["basics", "deeper", "further", "patterns", "results"]);

export function personalSectionRedirect(search = "", withinCouples = false) {
  const requested = new URLSearchParams(search).get("tab");
  const base = withinCouples ? "/app/couples" : "/app";
  if (requested === "personality") return `${base}/personal?tab=results`;
  if (requested === "intimacy") return `${base}/${requested}`;
  if (requested === "your-type") return withinCouples ? base : "/app/your-type";
  return null;
}

export function couplesOnly(member) {
  const access = member?.workspace_access;
  return access?.version === "workspace-access-v1" && access.mode === "couples"
    && access.couples_only === true && typeof access.pair_id === "string" && access.pair_id.length > 0;
}

export function assessmentLocation(search = "") {
  const redirect = personalSectionRedirect(search, true);
  if (redirect) return redirect;
  const requested = new URLSearchParams(search).get("tab");
  return "/app/couples/personal" + (PERSONAL_SECTIONS.has(requested) ? `?tab=${requested}` : "");
}

export function pairedProductRedirect(member, pathname, search = "") {
  if (!couplesOnly(member)) return null;
  const path = pathname.replace(/\/$/, "");
  if (path === "/app/settings" || path === "/app/settings/upgrade" || path === "/app/upgrade"
    || path === "/app/couples" || path.startsWith("/app/couples/")) return null;
  if (path === "/app/personal" || path === "/app/about") return assessmentLocation(search);
  if (path === "/app/personality") return "/app/couples/personal?tab=results";
  if (path === "/app/intimacy" || path === "/app/quick-advice") return path.replace("/app/", "/app/couples/");
  return path === "/app" || path.startsWith("/app/") ? "/app/couples" : null;
}
