import { useEffect, useRef, useState } from "react";

const MAX_W = 900;

function pixelate(ctx, img, r, scale) {
  ctx.drawImage(img, 0, 0, img.width * scale, img.height * scale);
  for (const b of r) {
    const tmp = document.createElement("canvas");
    const w = Math.max(1, Math.round(b.w / 14));
    const h = Math.max(1, Math.round(b.h / 14));
    tmp.width = w;
    tmp.height = h;
    tmp.getContext("2d").drawImage(ctx.canvas, b.x, b.y, b.w, b.h, 0, 0, w, h);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(tmp, 0, 0, w, h, b.x, b.y, b.w, b.h);
    ctx.imageSmoothingEnabled = true;
    ctx.fillStyle = "rgba(10,9,13,.55)";
    ctx.fillRect(b.x, b.y, b.w, b.h);
  }
}

// Draw blur boxes over a screenshot; exposes the redacted image as a JPEG data URL.
export function RedactCanvas({ file, onChange }) {
  const ref = useRef(null);
  const [img, setImg] = useState(null);
  const [unreadable, setUnreadable] = useState(false);
  const [boxes, setBoxes] = useState([]);
  const [drag, setDrag] = useState(null);

  useEffect(() => {
    const url = URL.createObjectURL(file);
    const i = new Image();
    i.onload = () => setImg(i);
    i.onerror = () => setUnreadable(true);
    i.src = url;
    return () => { i.onload = null; i.onerror = null; URL.revokeObjectURL(url); };
  }, [file]);

  useEffect(() => {
    if (!img) return;
    const scale = Math.min(1, MAX_W / img.width);
    const cv = ref.current;
    cv.width = img.width * scale;
    cv.height = img.height * scale;
    const ctx = cv.getContext("2d");
    pixelate(ctx, img, boxes, scale);
    if (drag) {
      ctx.strokeStyle = "#FF4D5E";
      ctx.lineWidth = 2;
      ctx.strokeRect(drag.x, drag.y, drag.w, drag.h);
    }
    if (!drag) onChange(cv.toDataURL("image/jpeg", 0.85));
  }, [img, boxes, drag]); // eslint-disable-line react-hooks/exhaustive-deps

  const pt = (e) => {
    const r = ref.current.getBoundingClientRect();
    return { x: (e.clientX - r.left) * (ref.current.width / r.width), y: (e.clientY - r.top) * (ref.current.height / r.height) };
  };
  const down = (e) => {
    e.preventDefault();
    const p = pt(e);
    setDrag({ x0: p.x, y0: p.y, x: p.x, y: p.y, w: 0, h: 0 });
  };
  const move = (e) => {
    if (!drag) return;
    const p = pt(e);
    setDrag((d) => ({ ...d, x: Math.min(d.x0, p.x), y: Math.min(d.y0, p.y), w: Math.abs(p.x - d.x0), h: Math.abs(p.y - d.y0) }));
  };
  const up = () => {
    if (drag && drag.w > 6 && drag.h > 6) setBoxes((b) => [...b, { x: drag.x, y: drag.y, w: drag.w, h: drag.h }]);
    setDrag(null);
  };

  return (
    <div data-testid="redact-canvas-wrap">
      {unreadable && <p role="alert" className="text-sm">This image couldn’t be opened. Remove it and choose another PNG, JPEG or WebP.</p>}
      <p className="text-xs text-[var(--muted)] mb-2">Drag over names, faces or photos to blur them on your device. Preview the image before sharing it.</p>
      <canvas ref={ref} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={up}
        className="block mx-auto max-w-full w-auto h-auto max-h-[55vh] rounded-lg border border-[var(--line)] touch-none cursor-crosshair" data-testid="redact-canvas" />
      <div className="flex gap-2 mt-2">
        <button type="button" className="text-xs btn-ghost px-3 py-1" onClick={() => setBoxes((b) => b.slice(0, -1))} disabled={!boxes.length} data-testid="redact-undo">Undo blur</button>
        <span className="text-xs text-[var(--muted)] self-center" data-testid="redact-count">{boxes.length} area(s) blurred</span>
      </div>
    </div>
  );
}
