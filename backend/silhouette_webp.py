"""Turn a white-background silhouette mp4 into a cropped, transparent, looping animated WebP.
Usage: python silhouette_webp.py in.mp4 out.webp [--seamless]"""
import subprocess
import sys

import imageio_ffmpeg
import numpy as np
from PIL import Image

W, FPS = 600, 24
H = next((int(a[4:]) for a in sys.argv if a.startswith("--h=")), 492)


def frames(path):
    raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-i", path, "-vf", f"fps={FPS},scale={W}:{H},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W)


def best_loop(alpha, min_len=48):
    small = alpha[:, ::4, ::4].astype(np.float32)
    n = len(small)
    return min(((np.abs(small[i] - small[j]).mean(), i, j) for i in range(min(40, n)) for j in range(i + min_len, n)))[1:]


def solid(a, max_hole=900, close=3):
    from scipy import ndimage
    fg = a > 100
    fg = ndimage.binary_closing(fg, structure=np.ones((close, close)), iterations=1)
    lab, n = ndimage.label(~fg)
    sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    small = [k + 1 for k, s in enumerate(sizes) if s <= max_hole and (k + 1) not in border]
    out = a.copy()
    out[np.isin(lab, small) | fg] = 255
    return out


def main(src, dst, seamless):
    gray = frames(src)
    alpha = np.clip((150 - gray.astype(np.int16)) * 6, 0, 255).astype(np.uint8)
    alpha = np.stack([solid(a) for a in alpha])
    if seamless:
        i, j = best_loop(alpha)
        alpha = alpha[i:j]
    ys, xs = np.where(alpha.max(axis=0) > 40)
    y0, y1, x0, x1 = max(ys.min() - 6, 0), min(ys.max() + 6, H), max(xs.min() - 6, 0), min(xs.max() + 6, W)
    out = []
    for a in alpha:
        im = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        im.putalpha(Image.fromarray(a[y0:y1, x0:x1]))
        out.append(im)
    out[0].save(dst, save_all=True, append_images=out[1:], duration=round(1000 / FPS), loop=0, quality=78, method=4)
    out[0].save(dst.replace(".webp", "-still.png"), optimize=True)
    print(dst, len(out), "frames")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--seamless" in sys.argv)
