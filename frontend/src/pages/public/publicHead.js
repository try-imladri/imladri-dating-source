import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { catalogArticleMetadata, publicSite } from "@/lib/publicCatalogMetadata.mjs";
import { articleRoute, isIndexablePath, normalizedPath, staticPageMetadata, pageStructuredData } from "@/lib/publicSeo.mjs";

function setTag(tag, attribute, key, value) {
  const selector = tag + "[" + attribute + '="' + key + '"]';
  const element = document.head.querySelector(selector) || document.createElement(tag);
  document.head.querySelectorAll(selector).forEach(other => { if (other !== element) other.remove(); });
  element.setAttribute(attribute, key);
  element.setAttribute(tag === "link" ? "href" : "content", value);
  if (!element.isConnected) document.head.appendChild(element);
}

const socialFields = ["type", "site_name", "title", "description", "url", "image", "image:alt", "locale"];
const twitterFields = ["card", "title", "description", "image", "image:alt"];
const robots = indexable => setTag("meta", "name", "robots", indexable && window.location.hostname === "imladri.com"
  ? "index, follow, max-image-preview:large" : "noindex, nofollow");

function updateHead(meta) {
  setTag("link", "rel", "canonical", meta.canonical);
  setTag("meta", "name", "description", meta.description);
  const values = [meta.type, "Imladri", meta.title, meta.description, meta.canonical, meta.image, meta.alt, "en_US"];
  socialFields.forEach((field, i) => setTag("meta", "property", "og:" + field, values[i]));
  const twitter = ["summary_large_image", meta.title, meta.description, meta.image, meta.alt];
  twitterFields.forEach((field, i) => setTag("meta", "name", "twitter:" + field, twitter[i]));
  const schema = document.getElementById("imladri-seo-schema") || document.createElement("script");
  schema.id = "imladri-seo-schema";
  schema.type = "application/ld+json";
  schema.textContent = JSON.stringify(pageStructuredData(meta));
  if (!schema.isConnected) document.head.appendChild(schema);
  robots(true);
}

function clearHead() {
  document.head.querySelector('link[rel="canonical"]')?.remove();
  twitterFields.forEach(field => document.head.querySelector('meta[name="twitter:' + field + '"]')?.remove());
  socialFields.forEach(field => document.head.querySelector('meta[property="og:' + field + '"]')?.remove());
  document.getElementById("imladri-seo-schema")?.remove();
  setTag("meta", "name", "description", "Imladri Personal");
}

export function useArticleHead(section, slug, item, failed) {
  useEffect(() => {
    if (item) {
      const meta = catalogArticleMetadata(section, slug, item);
      updateHead(meta);
      document.title = meta.title;
    } else if (failed || document.head.querySelector('link[rel="canonical"]')?.href !== publicSite + "/" + section + "/" + slug) {
      clearHead();
      robots(false);
    }
    return clearHead;
  }, [section, slug, item, failed]);
}

export function usePublicPageHead(pathname, hash = "") {
  useEffect(() => {
    const meta = staticPageMetadata(pathname);
    if (!meta || (normalizedPath(pathname) === "/support" && hash)) return;
    updateHead(meta);
    document.title = meta.title;
    return () => {
      clearHead();
      if (document.title === meta.title) document.title = "Imladri Personal";
    };
  }, [pathname, hash]);
}

// Runs outside authentication, including navigation from a public page into a
// member workspace. Never serialize private URLs, fragments or profile data.
export function RouteIndexing() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    // Successful article data owns its indexing state. A hash-only navigation
    // must never turn a failed/unpublished article back into an indexable page.
    if (articleRoute.test(pathname)) return;
    const indexable = isIndexablePath(pathname) && !(normalizedPath(pathname) === "/support" && hash);
    if (!indexable) {
      clearHead();
      document.title = normalizedPath(pathname) === "/support" ? "Private support request | Imladri" : "Your Imladri account";
    }
    robots(indexable);
  }, [pathname, hash]);
  return null;
}
