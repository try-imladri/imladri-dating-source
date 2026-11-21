import { useState } from "react";
import { ShieldCheck, ArrowLeft, ImagePlus } from "lucide-react";
import { RedactCanvas } from "@/components/app/RedactCanvas";

// Pick a screenshot, blur parts, preview exactly what will be read, then confirm.
export function SafeShot({ onConfirm, busy, label = "Read it", pickLabel = "Add a screenshot", compact }) {
  const [file, setFile] = useState(null);
  const [img, setImg] = useState(null);
  const [preview, setPreview] = useState(false);
  const reset = () => { setFile(null); setImg(null); setPreview(false); };
  if (!file) {
    return (
      <label className={`inline-flex items-center gap-1.5 btn-ghost cursor-pointer focus-within:outline ${compact ? "text-xs px-3 py-1.5" : "text-sm px-4 py-2"}`} data-testid="safeshot-pick">
        <ImagePlus className="w-3.5 h-3.5" />{pickLabel}
        <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" onChange={(e) => { setFile(e.target.files?.[0] || null); setPreview(false); }} data-testid="safeshot-input" />
      </label>
    );
  }
  return (
    <div className="w-full">
      {preview && (
      <div className="space-y-2 w-full" data-testid="safeshot-preview">
        <p className="text-xs inline-flex items-center gap-1.5 font-semibold"><ShieldCheck className="w-4 h-4 text-[#16a34a]" />DeepSeek will receive this version to transcribe the messages.</p>
        <img src={img} alt="What will be read" className="w-full max-h-[55vh] object-contain rounded-lg border-2 border-[#16a34a]" data-testid="safeshot-preview-image" />
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => setPreview(false)} className="text-xs btn-ghost px-3 py-1.5 inline-flex items-center gap-1" data-testid="safeshot-back"><ArrowLeft className="w-3 h-3" />Blur more</button>
          <button type="button" onClick={reset} className="text-xs px-3 py-1.5 text-[var(--muted)]" data-testid="safeshot-cancel">Remove</button>
          <span className="flex-1" />
          <button type="button" disabled={busy} onClick={async () => { if (await onConfirm(img) !== false) reset(); }} className="text-xs px-4 py-2 rounded-full bg-[#16a34a] text-white font-bold disabled:opacity-40" data-testid="safeshot-confirm">{busy ? "Reading…" : label}</button>
        </div>
      </div>
      )}
    <div className={`space-y-2 w-full ${preview ? "hidden" : ""}`} data-testid="safeshot-edit">
      <RedactCanvas file={file} onChange={setImg} />
      <div className="flex gap-2">
        <button type="button" onClick={reset} className="text-xs px-3 py-1.5 text-[var(--muted)]" data-testid="safeshot-cancel-edit">Remove</button>
        <span className="flex-1" />
        <button type="button" disabled={!img} onClick={() => setPreview(true)} className="text-xs px-4 py-2 rounded-full bg-[var(--ink)] text-[var(--bg)] font-bold disabled:opacity-40" data-testid="safeshot-preview-btn">Preview what will be read</button>
      </div>
    </div>
    </div>
  );
}
