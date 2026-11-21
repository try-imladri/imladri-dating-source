"""Refresh published name variants from Wikidata's CC0 structured data using the verified catalog IDs."""
import json
import time
from pathlib import Path

import requests

SEED = Path(__file__).resolve().parents[1] / "backend" / "seed"
LANGUAGES = "en|ar|bn|de|es|fa|fr|he|hi|ja|ko|pt|ru|uk|zh"
FIELDS = {"arts": "Arts and entertainment", "sport": "Sport", "voices": "Science, writing and public life", "politics": "Politics and government"}
LEGACY = {"elon-musk": "Technology and business", "travis-kelce": "Sport", "pete-davidson":"Arts and entertainment",
          "drake":"Arts and entertainment", "john-mayer":"Arts and entertainment", "leonardo-dicaprio":"Arts and entertainment"}
# Surface established aliases only when the linked Wikidata record actually supplies them.
RECOGNIZED = {"fdr","jfk","lbj","rbg","rfk","mlk","mlk jr.","aoc","pepe mujica","pepe","evita","evita perón",
              "earvin johnson","earvin johnson jr.","charles chaplin","charles spencer chaplin","cassius clay",
              "mohandas gandhi","mohandas karamchand gandhi","edson arantes do nascimento","aubrey drake graham",
              "maria skłodowska-curie","pele","volodymyr zelensky","vladimir zelensky","volodimir zelenski",
              "nelson rolihlahla mandela","madiba","queen latifah","prince rogers nelson"}

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def build():
    ids = read(SEED / "figure_person_ids.json")
    fields = dict(LEGACY)
    for path in sorted((SEED / "figure_profiles").glob("*.json")):
        for profile in read(path):
            fields[profile["slug"]] = FIELDS[path.stem.split("_")[0]]
    assert fields.keys() == ids.keys()
    profiles = {}
    items = sorted(ids.items())
    session = requests.Session()
    session.headers["User-Agent"] = "ImladriCatalog/1.0 (+https://imladri.com/support)"
    for start in range(0, len(items), 36):
        batch = items[start:start+36]
        response = session.get("https://www.wikidata.org/w/api.php", params={"action":"wbgetentities","format":"json",
            "ids":"|".join(qid for _,qid in batch),"props":"labels|aliases","languages":LANGUAGES}, timeout=45)
        response.raise_for_status()
        entities = response.json()["entities"]
        for slug, qid in batch:
            record = entities[qid]
            labels = {v["value"] for v in record["labels"].values()}
            names = {a["value"] for a in record.get("aliases",{}).get("en",[]) if a["value"].casefold() in RECOGNIZED}
            profiles[slug] = {"field":fields[slug], "names":sorted(labels | names), "source":qid}
        time.sleep(.15)
    target = SEED / "figure_search.json"
    target.write_text(json.dumps(profiles,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

if __name__ == "__main__":
    build()
