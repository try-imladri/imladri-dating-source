import { useEffect, useRef, useState } from "react";
import { useTheme } from "@/lib/theme";

const V = "?v=5";
const still = (src) => src.replace(".webp", "-still.png");
export const themed = (file, theme) => (theme === "dark" ? file.replace(/\.(webp|png)$/, "-w.$1") : file) + V;
const motionQuery = "(prefers-reduced-motion: reduce)";
const pageVisible = () => typeof document === "undefined" || document.visibilityState !== "hidden";

export function Silhouette({ src, className = "", testid, fast = false, paused = false }) {
  const ref = useRef(null);
  const { theme } = useTheme();
  const [visible, setVisible] = useState(true);
  const [reduce, setReduce] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(motionQuery)?.matches);
  const [activePage, setActivePage] = useState(pageVisible);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const query = window.matchMedia(motionQuery);
    if (!query) return undefined;
    const update = () => setReduce(query.matches);
    update();
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", update);
      return () => query.removeEventListener?.("change", update);
    }
    if (typeof query.addListener === "function") {
      query.addListener(update);
      return () => query.removeListener?.(update);
    }
    return undefined;
  }, []);
  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const update = () => setActivePage(pageVisible());
    update();
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof window.IntersectionObserver !== "function") return undefined;
    const io = new window.IntersectionObserver(([entry]) => {
      if (entry) setVisible(entry.isIntersecting);
    }, { threshold: 0.05 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  const playing = visible && !reduce && activePage && !paused;
  const file = playing ? (fast ? src.replace(".webp", "-fast.webp") : src) : still(src);
  return (
    <img ref={ref} src={themed(file, theme)} alt="" aria-hidden="true" data-playing={playing ? "true" : "false"} data-fast={fast ? "true" : "false"} data-theme={theme}
      className={`select-none pointer-events-none ${className}`} data-testid={testid} />
  );
}
