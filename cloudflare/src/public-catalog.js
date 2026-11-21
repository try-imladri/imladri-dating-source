import { catalogArticleMetadata, publicSite as site } from "../../frontend/src/lib/publicCatalogMetadata.mjs";
import { articleRoute, isPublicStaticPage, publicPages } from "../../frontend/src/lib/publicSeo.mjs";
import { renderHead } from "./seo.js";
const staticPages = Object.keys(publicPages);
const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escape = value => String(value).replace(/[&<>"']/g, char => entities[char]);

export function isPublicPage(path) {
  return isPublicStaticPage(path);
}

export function isCatalogDocument(path) {
  return path === "/sitemap.xml" || path === "/robots.txt" || articleRoute.test(path);
}

function response(body, type, request, status = 200) {
  const headers = { "Content-Type": type + "; charset=utf-8", "Cache-Control": "no-store" };
  if (new URL(request.url).hostname !== "imladri.com" || status !== 200) headers["X-Robots-Tag"] = "noindex";
  if (status === 503) headers["Retry-After"] = "30";
  return new Response(request.method === "HEAD" ? null : body, { status, headers });
}

export async function catalogDocument(request, assets, read) {
  const { pathname, hostname } = new URL(request.url);
  if (pathname === "/robots.txt") {
    const policy = hostname === "imladri.com"
      // Private HTML may be crawled so its noindex header is actually observed.
      // Authentication, rather than robots.txt, protects member data.
      ? "User-agent: *\nAllow: /\nDisallow: /api/\nAllow: /api/media/\nSitemap: " + site + "/sitemap.xml\n"
      : "User-agent: *\nDisallow: /\n";
    return response(policy, "text/plain", request);
  }
  try {
    if (pathname === "/sitemap.xml") {
      const result = await read("/api/public/catalog-paths");
      if (!result.ok) return response("Catalog temporarily unavailable", "text/plain", request, 503);
      const paths = [...new Set([...staticPages, ...(await result.json()).filter(path => articleRoute.test(path))])];
      return response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
        paths.map(path => "<url><loc>" + escape(site + path) + "</loc></url>").join("") + "</urlset>", "application/xml", request);
    }
    const [, section, slug] = pathname.match(articleRoute);
    const [result, shell] = await Promise.all([
      read("/api/public/" + (section === "types" ? "archetypes" : "figures") + "/" + slug),
      assets.fetch(new Request(new URL("/", request.url), {headers: {Accept: "text/html"}}))
    ]);
    if (result.status === 404) return response('<!doctype html><html lang="en"><title>Article unavailable · Imladri</title><main><h1>This article is unavailable</h1><a href="/' + section + '">Browse the directory</a></main></html>', "text/html", request, 404);
    if (!result.ok || !shell.ok) return response("Article temporarily unavailable", "text/plain", request, 503);
    const item = await result.json();
    const article = catalogArticleMetadata(section, slug, item);
    const summary = escape(article.description);
    const html = renderHead(await shell.text(), article, hostname === "imladri.com")
      .replace('<div id="root"></div>', () => '<div id="root"><main><h1>' + escape(item.name) + "</h1><p>" + summary +
        '</p><a href="/' + section + '">Browse the directory</a></main></div>');
    return response(html, "text/html", request);
  } catch {
    return response("Catalog temporarily unavailable", "text/plain", request, 503);
  }
}
