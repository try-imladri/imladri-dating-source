import { useEffect, useRef, useState } from "react";
import { Silhouette } from "@/components/public/Silhouette";
import "./FeatureIllustration.css";

export const FEATURE_SCENES = {
  couples: "/img/features/couples-v1.png",
  individual: "/img/features/individual-v1.png",
  intimacy: "/img/features/intimacy-v1.png",
  communication: "/img/features/communication-v1.png",
};

const motionQuery = "(prefers-reduced-motion: reduce)";
const pageVisible = () => typeof document === "undefined" || document.visibilityState !== "hidden";

export function FeatureIllustration({ feature, paused = false, className = "", testid }) {
  const ref = useRef(null);
  const [inView, setInView] = useState(true);
  const [activePage, setActivePage] = useState(pageVisible);
  const [reduced, setReduced] = useState(() => !!window.matchMedia?.(motionQuery)?.matches);

  useEffect(() => {
    const query = window.matchMedia?.(motionQuery);
    if (!query) return undefined;
    const update = () => setReduced(query.matches);
    update();
    if (query.addEventListener) query.addEventListener("change", update);
    else query.addListener?.(update);
    return () => {
      if (query.removeEventListener) query.removeEventListener("change", update);
      else query.removeListener?.(update);
    };
  }, []);
  useEffect(() => {
    const update = () => setActivePage(pageVisible());
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  useEffect(() => {
    if (!ref.current || typeof window.IntersectionObserver !== "function") return undefined;
    const observer = new window.IntersectionObserver(([entry]) => {
      if (entry) setInView(entry.isIntersecting);
    }, { threshold: 0.05 });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  const playing = !paused && !reduced && activePage && inView;
  const scene = FEATURE_SCENES[feature];

  return <div ref={ref} className={`feature-illustration ${className}`} aria-hidden="true"
    data-testid={testid} data-feature={scene ? feature : "your-type"} data-playing={playing ? "true" : "false"}>
    {scene ? <div className={`feature-scene feature-scene--${feature}`}>
      <img className="feature-person feature-person--left" src={scene} alt="" draggable={false} decoding="async" />
      <img className="feature-person feature-person--right" src={scene} alt="" draggable={false} decoding="async" />
      {feature === "individual" && <div className="feature-reflection-glint" style={{ maskImage: `url(${scene})`, WebkitMaskImage: `url(${scene})` }} />}
      {feature === "communication" && <svg className="feature-speech" viewBox="0 0 100 100" fill="none" aria-hidden="true">
        <g className="feature-speech--left">
          <path d="M17 3h24a4 4 0 0 1 4 4v7a4 4 0 0 1-4 4h-9l-6 5v-5h-9a4 4 0 0 1-4-4V7a4 4 0 0 1 4-4Z" />
          <circle cx="23" cy="10.5" r="1" /><circle cx="29" cy="10.5" r="1" /><circle cx="35" cy="10.5" r="1" />
        </g>
        <g className="feature-speech--right">
          <path d="M60 6h24a4 4 0 0 1 4 4v7a4 4 0 0 1-4 4h-9v5l-6-5h-9a4 4 0 0 1-4-4v-7a4 4 0 0 1 4-4Z" />
          <circle cx="66" cy="13.5" r="1" /><circle cx="72" cy="13.5" r="1" /><circle cx="78" cy="13.5" r="1" />
        </g>
      </svg>}
    </div> : <Silhouette src="/img/couple-walk.webp" paused={!playing} className="feature-original-walk" testid="home-original-walking-couple" />}
  </div>;
}
