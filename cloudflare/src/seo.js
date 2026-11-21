import { isIndexablePath, staticPageMetadata, pageStructuredData } from "../../frontend/src/lib/publicSeo.mjs";

const entities = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
export const escapeHtml = value => String(value).replace(/[&<>"']/g, char => entities[char]);
export const indexPolicy = (request, status = 200) => new URL(request.url).hostname === "imladri.com"
  && status === 200 && isIndexablePath(new URL(request.url).pathname);

export function renderHead(html, meta, indexable) {
  // Replace owned tags instead of accumulating home-page tags on article responses.
  const stripped = html.replace(/<title\b[^>]*>[\s\S]*?<\/title>/gi, "")
    .replace(/<meta\b[^>]*(?:name\s*=\s*["'](?:description|robots|twitter:[^"']+)["']|property\s*=\s*["']og:[^"']+["'])[^>]*>/gi, "")
    .replace(/<link\b[^>]*rel\s*=\s*["']canonical["'][^>]*>/gi, "")
    .replace(/<script\b[^>]*id\s*=\s*["']imladri-seo-schema["'][^>]*>[\s\S]*?<\/script>/gi, "");
  let tags = '<title>' + escapeHtml(meta.title) + '</title><meta name="description" content="' + escapeHtml(meta.description) + '"/>'
    + '<meta name="robots" content="' + (indexable ? 'index, follow, max-image-preview:large' : 'noindex, nofollow') + '"/>';
  if (meta.canonical) {
    tags += '<link rel="canonical" href="' + escapeHtml(meta.canonical) + '"/>';
    const fields = {type: meta.type, site_name: "Imladri", title: meta.title, description: meta.description,
      url: meta.canonical, image: meta.image, "image:alt": meta.alt, locale: "en_US"};
    for (const [key, value] of Object.entries(fields)) tags += '<meta property="og:' + key + '" content="' + escapeHtml(value) + '"/>';
    const twitter = {card: "summary_large_image", title: meta.title, description: meta.description, image: meta.image, "image:alt": meta.alt};
    for (const [key, value] of Object.entries(twitter)) tags += '<meta name="twitter:' + key + '" content="' + escapeHtml(value) + '"/>';
    const schema = JSON.stringify(pageStructuredData(meta)).replace(/</g, "\\u003c");
    tags += '<script type="application/ld+json" id="imladri-seo-schema">' + schema + '</script>';
  }
  return stripped.replace(/<\/head>/i, () => tags + "</head>");
}

export function htmlResponse(html, request, status = 200, headers = {}) {
  const resultHeaders = new Headers(headers);
  resultHeaders.set("Content-Type", "text/html; charset=utf-8");
  resultHeaders.set("Cache-Control", "no-store");
  resultHeaders.delete("Content-Length");
  resultHeaders.delete("ETag");
  if (!indexPolicy(request, status)) resultHeaders.set("X-Robots-Tag", "noindex, nofollow");
  return new Response(request.method === "HEAD" ? null : html, {status, headers: resultHeaders});
}

export async function pageDocument(request, assets, knownPrivate = false) {
  const url = new URL(request.url);
  const meta = staticPageMetadata(url.pathname);
  const status = meta || knownPrivate ? 200 : 404;
  const shell = await assets.fetch(new Request(new URL("/", url), {headers: {Accept: "text/html"}}));
  if (!shell.ok) return htmlResponse("Site temporarily unavailable", request, 503, {"Retry-After": "30"});
  const privateMeta = {title: status === 404 ? "Page not found | Imladri" : "Your Imladri account",
    description: status === 404 ? "This page could not be found. Visit Imladri to explore the app." : "Sign in to access your private Imladri workspace."};
  let html = renderHead(await shell.text(), meta || privateMeta, indexPolicy(request, status));
  if (status === 404) html = html.replace('<div id="root"></div>', '<div id="root"><main><h1>Page not found</h1><a href="/">Visit Imladri</a></main></div>');
  return htmlResponse(html, request, status, shell.headers);
}
