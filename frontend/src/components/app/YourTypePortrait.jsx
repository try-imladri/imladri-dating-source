import { useId } from "react";

export default function YourTypePortrait({ face, className = "", label }) {
  const clipId = useId().replace(/:/g, "");
  const sprite = face?.sprite;
  const rect = sprite?.rect;
  const fullBody = face?.image_kind === "full_body";
  const contain = fullBody || face?.display_fit === "contain";
  const description = label || (fullBody ? "Original synthetic adult full-body image" : "Original synthetic adult portrait");
  if (!face?.url) return <span className={`block bg-[var(--surface)] ${className}`} aria-label="Portrait unavailable" />;
  if (!rect) return <img src={face.url} alt={description} className={`block w-full h-full ${contain ? "object-contain" : "object-cover"} ${className}`} />;
  return <svg role="img" aria-label={description} viewBox={`${rect.x} ${rect.y} ${rect.w} ${rect.h}`}
    className={`block w-full h-full ${className}`} preserveAspectRatio={contain ? "xMidYMid meet" : "xMidYMid slice"}>
    <defs><clipPath id={clipId}><rect x={rect.x} y={rect.y} width={rect.w} height={rect.h} /></clipPath></defs>
    <image href={face.url} width={sprite.sheet_width} height={sprite.sheet_height} clipPath={`url(#${clipId})`} />
  </svg>;
}
