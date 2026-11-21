"""Inventory one exact Docker image and the frontend lockfile against public advisories."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
OSV = "https://api.osv.dev/v1/"
NPM = "https://registry.npmjs.org/-/npm/v1/security/advisories/bulk"
IMAGE_PROBE = """
from importlib import metadata
from pathlib import Path
import hashlib, json, platform, struct, subprocess
from backend.human_review import reader_html
from backend.intimacy_catalog import questions
base=Path('/app/backend/seed')
formats={'reader_ballot.html': b'<!doctype html>', 'prompt-transcription-sample.png': bytes.fromhex('89504e470d0a1a0a')}
for name,prefix in formats.items():
    assert (base/name).read_bytes().startswith(prefix), name
seed = lambda name: json.loads((base/(name+'.json')).read_bytes())
figures = list(seed("figures")) + [p["slug"] for f in (base/"figure_profiles").glob("*.json") for p in json.loads(f.read_bytes())]
men, women, index = seed("male_archetypes"), seed("female_archetypes"), seed("figure_search")
assert len(figures)==len(set(figures)) and set(figures)==set(index)
assert not set(men).intersection(women)
html=reader_html("A")
assert "__PACKET__" not in html and "definitions" in html
sample=(base/"prompt-transcription-sample.png").read_bytes()
catalog = {"public_figures":len(figures), "indexed_figures":len(index), "archetypes":len(men)+len(women),
           "male_archetypes":len(men), "female_archetypes":len(women), "intimacy_questions":len(questions()),
           "intimacy_sections":sum(map(len,seed("intimacy").values())), "couples_cards":len(seed("couples_cards")),
           "reader_bytes":len(html.encode()), "transcription_dimensions":struct.unpack(">II",sample[16:24])}
canonical_ballot=hashlib.sha256((base/"reader_ballot.html").read_bytes().replace(bytes([13,10]),bytes([10]))).hexdigest()
query=subprocess.check_output(['dpkg-query','-W','-f',r'${binary:Package}\\t${Version}\\t${source:Package}\\t${source:Version}\\n'],text=True)
packages=[line.split('\\t') for line in query.splitlines()]
release=platform.freedesktop_os_release()
assert release.get('ID')=='debian' and release['VERSION_ID'].split('.')[0].isdigit()
print(json.dumps({'operating_system':{'id':release['ID'],'version':release['VERSION_ID'],'codename':release['VERSION_CODENAME']},'catalog':catalog,'ballot_lf_sha256':canonical_ballot,'pypi':sorted({(d.metadata['Name'],d.version) for d in metadata.distributions()}),'debian_binaries':sorted((name,version) for name,version,_,_ in packages),'debian_sources':sorted({(source,version) for _,_,source,version in packages}), 'study_assets':{name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in formats}}))
"""


def command(args, **kwargs):
    return subprocess.check_output(args, text=True, cwd=ROOT, **kwargs)


def node_graph():
    lock = (ROOT / "frontend" / "yarn.lock").read_bytes()
    packages = {}
    for block in re.split(r"\n\s*\n", lock.decode("utf-8").replace("\r\n","\n")):
        version = re.search(r'^  version "([^"]+)"', block, re.M)
        if not version:
            continue
        resolved = re.search(r'^  resolved "([^"]+)"', block, re.M)
        if not resolved:
            raise ValueError("Missing resolved version in lockfile")
        package, archive = unquote(urlparse(resolved[1]).path.lstrip("/")).rsplit("/-/",1)
        packages.setdefault(package,set()).add(version[1])
    return hashlib.sha256(lock).hexdigest(), {k:sorted(v) for k,v in sorted(packages.items())}


def osv(ecosystem, packages):
    records = []
    for offset in range(0,len(packages),100):
        batch = packages[offset:offset+100]
        queries=[{"package":{"name":n,"ecosystem":ecosystem},"version":v} for n,v in batch]
        response=requests.post(OSV+"querybatch",json={"queries":queries},timeout=45)
        response.raise_for_status()
        results=response.json()["results"]
        if len(results) != len(batch):
            raise ValueError("Incomplete advisory batch")
        for (name, version), query, result in zip(batch,queries,results):
            while True:
                for item in result.get("vulns",[]):
                    records.append({"name":name,"version":version,"advisory":item["id"],"modified":item.get("modified")})
                if not result.get("next_page_token"):
                    break
                response=requests.post(OSV+"query", json={**query,"page_token":result["next_page_token"]},timeout=45)
                response.raise_for_status()
                result=response.json()
    return records


def npm(packages):
    response=requests.post(NPM,json=packages,timeout=45)
    response.raise_for_status()
    return response.json()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image",help="Local image tag or deployed registry digest")
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    image=json.loads(command(["docker","image","inspect",args.image]))[0]
    data=json.loads(command(["docker","run","--rm","--read-only","--network=none","--cap-drop=ALL","--security-opt=no-new-privileges","--entrypoint","python",image["Id"],"-c",IMAGE_PROBE]))
    for name, digest in data["study_assets"].items():
        expected = (ROOT/"backend/seed"/name).read_bytes()
        if name == "reader_ballot.html":
            expected = expected.replace(b"\r\n", b"\n")
            digest = data["ballot_lf_sha256"]
        if hashlib.sha256(expected).hexdigest()!=digest:
            raise ValueError("Packaged study asset differs from checkout: "+name)
    lock_hash, packages=node_graph()
    debian_ecosystem="Debian:"+data["operating_system"]["version"].split(".")[0]
    result={"checked_utc":datetime.now(timezone.utc).isoformat(),"image_id":image["Id"],"image_digests":image.get("RepoDigests") or [],
            "yarn_lock_sha256":lock_hash,"inventory":{**data,"npm_lock":packages},"sources":{"OSV":OSV,"npm":NPM,"debian_ecosystem":debian_ecosystem},
            "advisories":{},"errors":{}}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={"pypi":pool.submit(osv,"PyPI",data["pypi"]),"debian_sources":pool.submit(osv,debian_ecosystem,data["debian_sources"]),"npm_lock":pool.submit(npm,packages)}
        for key,future in futures.items():
            try:
                result["advisories"][key]=future.result()
            except (requests.RequestException,KeyError,ValueError) as exc:
                result["errors"][key]=type(exc).__name__
    args.output.write_text(json.dumps(result,indent=2),encoding="utf8")
    print("Exact image",image["Id"],"inventory", {key:len(result["inventory"][key]) for key in ("pypi","debian_binaries","debian_sources","npm_lock")})
    print("Advisory counts by ecosystem", {k:sum(len(v) for v in value.values()) if isinstance(value,dict) else len(value) for k,value in result["advisories"].items()}, "failed sources",sorted(result["errors"]))
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
