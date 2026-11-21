"""One-off: animate the hero silhouettes with fal.ai image-to-video. Run: EMERGENT_LLM_KEY=... python animate.py"""
import os
import sys
import time

import requests

# Fal Universal Key supports queue inference through Emergent proxy, not Platform APIs.
KEY = os.environ["EMERGENT_LLM_KEY"]
BASE = os.environ.get("INTEGRATION_PROXY_URL", "https://integrations.emergentagent.com").rstrip("/") + "/api/v1/fal"
ENDPOINT = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
SITE = sys.argv[1]

JOBS = {
    "walk": (f"{SITE}/img/couple-white.jpg",
             "The black silhouette couple walks forward naturally holding hands, legs stepping and arms swinging in a smooth walk cycle, "
             "the woman's hair and dress flow gently. They stay in place in the frame as if on a treadmill. Static camera, plain white background, flat black silhouettes only."),
    "dance": (f"{SITE}/img/couple-dance-white.jpg",
              "The black silhouette couple dances gracefully, the man twirls the woman, her dress and hair swirl, smooth elegant ballroom motion, "
              "they stay centered in the frame. Static camera, plain white background, flat black silhouettes only."),
    "reunion": ("https://static.prod-images.emergentagent.com/jobs/38b15f42-c1dc-486e-bd0a-5e1f80613408/images/c9edda4b86b5b5ccea8cab639f29db228a3581ffc80e8dbfb5d0a21bd6c30045.jpeg",
                "The black silhouette couple embraces in a joyful reunion, he lifts her and slowly spins her around in a circle, her hair and dress flow, then sets her down and they hold each other, "
                "smooth gentle motion, they stay centered in the frame. Static camera, plain white background, flat black silhouettes only."),
    "man": ("https://static.prod-images.emergentagent.com/jobs/38b15f42-c1dc-486e-bd0a-5e1f80613408/images/12d4b70ebef494925841f833f6d6f4665b7eb79e384d42eb1276d781c309d95d.jpeg",
            "The black silhouette man walks forward confidently in a smooth natural walk cycle, legs stepping and free arm swinging, staying in place in the frame as if on a treadmill. "
            "Static camera, plain white background, flat black silhouette only."),
    "woman": ("https://static.prod-images.emergentagent.com/jobs/38b15f42-c1dc-486e-bd0a-5e1f80613408/images/b7f61bd42b50028e53b14eb4ed178f8c5c6d8d62dabea5f00ff769ab1b0120b0.jpeg",
              "The black silhouette woman walks forward confidently in a smooth natural walk cycle, legs stepping, arms swinging, hair and dress flowing, staying in place in the frame as if on a treadmill. "
              "Static camera, plain white background, flat black silhouette only."),
}


def submit(name, url, prompt):
    r = requests.post(f"{BASE}/proxy", headers={**H, "X-Fal-Target-Url": f"https://queue.fal.run/{ENDPOINT}"},
                      json={"prompt": prompt, "image_url": url, "duration": "5", "negative_prompt": "camera movement, zoom, color, texture, background objects, text"}, timeout=60)
    r.raise_for_status()
    return r.json()


def wait(sub):
    for _ in range(300):
        s = requests.get(sub["status_url"], headers=H, timeout=30).json()
        st = (s.get("status") or "").upper()
        if st in {"COMPLETED", "OK"}:
            return requests.get(sub["response_url"], headers=H, timeout=60).json()
        if st in {"FAILED", "CANCELLED", "ERROR"}:
            raise RuntimeError(s)
        time.sleep(4)
    raise RuntimeError("timeout")


subs = {n: submit(n, u, p) for n, (u, p) in JOBS.items() if n in (sys.argv[2:] or JOBS)}
for n, sub in subs.items():
    out = wait(sub)
    vid = requests.get(out["video"]["url"], timeout=120).content
    open(f"/tmp/{n}.mp4", "wb").write(vid)
    print("ok", n, len(vid), flush=True)
