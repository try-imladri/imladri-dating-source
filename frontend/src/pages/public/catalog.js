import { useEffect, useState } from "react";
import { publicApi } from "@/lib/api";
import { useLocation } from "react-router-dom";
import { staticPageMetadata } from "@/lib/publicSeo.mjs";
import { getCachedPublicCatalog, invalidatePublicCatalog, isPublicCatalogPath, loadPublicCatalog } from "@/lib/publicCatalogCache";

export function useCatalogEntry(path) {
  const [result, setResult] = useState(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const request = isPublicCatalogPath(path)
      ? loadPublicCatalog(path, { signal: controller.signal })
      : publicApi.get(path, { signal: controller.signal }).then(({ data }) => data);
    request.then(
      data => { if (!controller.signal.aborted) setResult({ path, attempt, data }); },
      error => {
        if (controller.signal.aborted) return;
        const missing = error.response?.status === 404;
        if (missing) {
          if (path.startsWith("/public/archetypes/")) invalidatePublicCatalog("/public/archetypes");
          if (path.startsWith("/public/figures/")) invalidatePublicCatalog("/public/figures");
        }
        setResult({ path, attempt, missing, failed: true });
      },
    );
    return () => controller.abort();
  }, [path, attempt]);
  const latest = result?.path === path && result.attempt === attempt ? result : { data: getCachedPublicCatalog(path) };
  return { ...latest, retry: () => { invalidatePublicCatalog(path); setAttempt(n => n + 1); } };
}

export function CatalogRetry({ retry }) {
  return <div className="px-5 sm:px-8 py-12" role="alert">
    <p>The library couldn’t load. Check your connection and try again.</p>
    <button className="mt-3 underline" onClick={retry}>Try again</button>
  </div>;
}

export function useCatalogTitle(title) {
  const { pathname } = useLocation();
  useEffect(() => {
    const previous = document.head.querySelector('meta[property="og:type"][content="article"]') ? "Imladri Personal" : document.title;
    const directory = ["/types", "/figures", "/plans"].includes(pathname.replace(/\/$/, ""));
    document.title = (directory && staticPageMetadata(pathname)?.title) || `${title} — Imladri`;
    return () => { document.title = previous; };
  }, [title, pathname]);
}
