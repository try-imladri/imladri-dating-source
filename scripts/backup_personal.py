"""Back up the Imladri Personal production database using this Windows user's DPAPI.

From the project Python environment:
  python scripts/backup_personal.py backup
  python scripts/backup_personal.py restore-verify BACKUP_FILENAME

Requires the authenticated Atlas CLI and local Docker Desktop with mongo:8.0.
Set IMLADRI_BACKUP_MONGO_URI in the launching environment or use the existing
ignored .local/personal-live-server.env. Only encrypted artifacts go to
.local/backups. Keep an offline copy of the artifact and the Windows DPAPI
user profile needed to decrypt it. Restore verification always uses a new,
loopback-only MongoDB with RAM-backed storage, then removes it.
"""
import argparse
import ctypes
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import struct
import subprocess
import sys
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import requests
from bson import BSON, json_util
from dotenv import dotenv_values
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
BACKUPS = LOCAL / "backups"
PROJECT = "6aefd7b0aa04177eba70c615"
CLUSTER = "imladri-personal-live"
DATABASE = "imladri_personal_live"
IMAGE = "mongo:8.0"
FORMAT = b"IMLADRI-PERSONAL-DPAPI\x01"
CHUNK = 1024 * 1024


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command, timeout=90):
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    require(result.returncode == 0, f"{Path(command[0]).stem} {command[1]} failed (exit {result.returncode}).")
    return result.stdout


def docker(*args):
    return run(["docker", *args])


def atlas(*args, read_json=True):
    exe = shutil.which("atlas") or str(LOCAL / "provider-tools" / "atlas.exe")
    out = run([exe, *args, "--projectId", PROJECT, *(["--output", "json"] if read_json else [])])
    return json.loads(out) if read_json else None


def source_uri():
    cfg = dotenv_values(LOCAL / "personal-live-server.env")
    uri = os.environ.get("IMLADRI_BACKUP_MONGO_URI") or cfg.get("MONGO_URL") or ""
    cluster = atlas("clusters", "describe", CLUSTER)
    require(cluster["groupId"] == PROJECT and cluster["name"] == CLUSTER, "Unexpected Atlas deployment.")
    expected = urlsplit(cluster["connectionStrings"]["standardSrv"]).hostname
    actual = urlsplit(uri)
    require(actual.scheme == "mongodb+srv" and actual.hostname == expected and
            actual.path in ("", "/", "/" + DATABASE), "The supplied credential does not target Personal production.")
    for key, value in parse_qsl(actual.query):
        name = key.lower()
        if name in ("tls", "ssl"):
            require(value.lower() == "true", "Backup connections require TLS.")
        if name in ("tlsinsecure", "tlsallowinvalidcertificates", "tlsallowinvalidhostnames"):
            require(value.lower() == "false", "Backup connections must authenticate the Atlas certificate.")
    return uri


def rules():
    return atlas("accessLists", "list")["results"]


def network(entry):
    value = entry.get("cidrBlock") or entry.get("ipAddress")
    return ipaddress.ip_network(value, strict=False) if value else None


@contextmanager
def workstation_access(uri):
    try:
        with MongoClient(uri, serverSelectionTimeoutMS=2500) as probe:
            probe.admin.command("ping")
    except ServerSelectionTimeoutError:
        pass
    else:
        yield
        return
    response = requests.get("https://api.ipify.org?format=json", timeout=10)
    response.raise_for_status()
    address = ipaddress.IPv4Address(response.json()["ip"])
    entries = rules()
    if any(net and net.version == 4 and address in net for net in map(network, entries)):
        yield
        return
    marker = "imladri-personal-backup-" + secrets.token_hex(10)
    until = (datetime.now(timezone.utc) + timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        atlas("accessLists", "create", str(address), "--type", "ipAddress", "--comment", marker, "--deleteAfter", until)
        require(any(e.get("comment") == marker and network(e) == ipaddress.ip_network(f"{address}/32")
                    for e in rules()), "Workstation access could not be verified.")
        yield
    finally:
        owned = [e for e in rules() if e.get("comment") == marker]
        if owned:
            require(len(owned) == 1 and network(owned[0]) == ipaddress.ip_network(f"{address}/32"),
                    "Temporary access changed; its Atlas expiry is still in place.")
            atlas("accessLists", "delete", str(address), "--force", read_json=False)
        require(not any(e.get("comment") == marker for e in rules()), "Temporary Atlas access is still present.")


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def dpapi(data, entropy, decrypt=False):
    src_buf, salt_buf = ctypes.create_string_buffer(data), ctypes.create_string_buffer(entropy)
    src = Blob(len(data), ctypes.cast(src_buf, ctypes.c_void_p))
    salt = Blob(len(entropy), ctypes.cast(salt_buf, ctypes.c_void_p))
    out = Blob()
    library = ctypes.windll.crypt32
    call = library.CryptUnprotectData if decrypt else library.CryptProtectData
    require(call(ctypes.byref(src), None, ctypes.byref(salt), None, None, 1, ctypes.byref(out)),
            "Windows could not authenticate/decrypt this backup or protect its contents.")
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(out.data))


def manifest(db):
    result = {}
    for collection in sorted(db.list_collections(), key=lambda item: item["name"]):
        name = collection["name"]
        if name.startswith("system."):
            continue
        options = collection.get("options", {})
        if collection.get("type") == "view":
            # The dump stores the definition. Time-dependent views can return different rows on restore.
            result[name] = {"type": "view", "options": options, "count": 0, "indexes": {},
                            "digest": hashlib.sha256(BSON.encode(options)).hexdigest()}
            continue
        digest = hashlib.sha256()
        count = 0
        for document in db[name].find().sort("_id", 1):
            digest.update(BSON.encode(document))
            count += 1
        indexes = {}
        for key, spec in db[name].index_information().items():
            indexes[key] = {k: v for k, v in spec.items() if k not in ("ns", "v")}
        result[name] = {"count": count, "digest": digest.hexdigest(), "indexes": indexes, "options": options}
    # Normalize BSON scalar types and tuple/list representations for archive comparison.
    return json.loads(json_util.dumps(result))


def write_frame(file, nonce, sequence, clear):
    encrypted = dpapi(clear, nonce + sequence.to_bytes(8, "big"))
    file.write(struct.pack(">I", len(encrypted)))
    file.write(encrypted)


def read_frame(file, nonce, sequence):
    size_raw = file.read(4)
    require(len(size_raw) == 4, "Truncated backup.")
    size, = struct.unpack(">I", size_raw)
    require(0 < size <= 32 * CHUNK, "Invalid backup frame length.")
    encrypted = file.read(size)
    require(len(encrypted) == size, "Truncated backup frame.")
    return dpapi(encrypted, nonce + sequence.to_bytes(8, "big"), decrypt=True)


def dump(file, uri, expected):
    nonce = secrets.token_bytes(16)
    file.write(FORMAT + nonce)
    header = {"project": PROJECT, "cluster": CLUSTER, "database": DATABASE, "manifest": expected}
    write_frame(file, nonce, 0, json.dumps(header, separators=(",", ":")).encode())
    # The config is supplied through stdin and exists only in container shared memory.
    command = ["docker", "run", "--rm", "-i", "--log-driver=none", "--read-only", "--entrypoint", "sh", IMAGE, "-ec",
               'umask 077; cat > /dev/shm/config; exec mongodump --config=/dev/shm/config --db "$1" --gzip --archive',
               "_", DATABASE]
    digest = hashlib.sha256()
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        try:
            proc.stdin.write(json.dumps({"uri": uri}).encode())
            proc.stdin.close()
            seq = 1
            while chunk := proc.stdout.read(CHUNK):
                digest.update(chunk)
                write_frame(file, nonce, seq, b"D" + chunk)
                seq += 1
            require(proc.wait(timeout=30) == 0 and seq > 1, "MongoDB dump failed or was empty.")
            write_frame(file, nonce, seq, b"E" + digest.digest())
        finally:
            if proc.poll() is None:
                proc.kill()


@contextmanager
def isolated_mongo():
    name = "imladri-restore-" + secrets.token_hex(10)
    # Only this newly created local container can be the restore target. No host data volumes.
    cid = docker("run", "-d", "--name", name, "--log-driver=none", "--tmpfs", "/data/db:rw,size=1g",
                 "--tmpfs", "/data/configdb:rw,size=64m", "-p", "127.0.0.1::27017", IMAGE,
                 "--bind_ip_all", "--setParameter", "ttlMonitorEnabled=false").decode().strip()
    try:
        mapping = docker("port", cid, "27017/tcp").decode().strip()
        host, port = mapping.split(":")
        require(host == "127.0.0.1" and port.isdigit(), "Refusing a non-loopback restore target.")
        with MongoClient(f"mongodb://127.0.0.1:{int(port)}/?directConnection=true", serverSelectionTimeoutMS=35000) as client:
            client.admin.command("ping")
            require(DATABASE not in client.list_database_names(), "Restore target is not empty.")
            yield cid, client[DATABASE]
    finally:
        docker("rm", "-f", "-v", cid)


def verify(path):
    with path.open("rb") as file:
        require(file.read(len(FORMAT)) == FORMAT, "Unrecognized backup format.")
        nonce = file.read(16)
        header = json.loads(read_frame(file, nonce, 0))
        require((header["project"], header["cluster"], header["database"]) == (PROJECT, CLUSTER, DATABASE),
                "Archive is not a Personal production backup.")
        with isolated_mongo() as (cid, db):
            command = ["docker", "exec", "-i", cid, "mongorestore", "--host=127.0.0.1:27017",
                       "--nsInclude=" + DATABASE + ".*", "--stopOnError", "--archive", "--gzip"]
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
                try:
                    seq = 1
                    digest = hashlib.sha256()
                    while True:
                        frame = read_frame(file, nonce, seq)
                        if frame.startswith(b"E"):
                            require(frame[1:] == digest.digest() and not file.read(1), "Archive checksum mismatch.")
                            break
                        require(frame.startswith(b"D"), "Invalid archive frame.")
                        digest.update(frame[1:])
                        proc.stdin.write(frame[1:])
                        seq += 1
                    proc.stdin.close()
                    require(proc.wait(timeout=180) == 0, "MongoDB restore failed.")
                finally:
                    if proc.poll() is None:
                        proc.kill()
            restored = manifest(db)
        require(restored == header["manifest"], "Restored data, collection options or indexes differ; rerun during a quiet interval.")
    return len(restored), sum(row["count"] for row in restored.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("backup")
    commands.add_parser("restore-verify").add_argument("file", help="Filename in .local/backups")
    args = parser.parse_args()
    require(os.name == "nt", "A Windows user profile is required.")
    endpoint = os.environ.get("DOCKER_HOST") or docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}").decode().strip()
    require(endpoint.lower().startswith("npipe:////./pipe/"), "Refusing a nonlocal Docker engine.")
    docker("image", "inspect", IMAGE, "--format", "{{.Id}}")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    if args.action == "restore-verify":
        path = (BACKUPS / args.file).resolve()
        require(path.parent == BACKUPS.resolve() and path.suffix == ".dpapi", "Select an encrypted backup in .local/backups.")
        collections, docs = verify(path)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = BACKUPS / f"{DATABASE}-{stamp}-{secrets.token_hex(3)}.dpapi"
        pending = path.with_suffix(".pending")
        uri = source_uri()
        try:
            with workstation_access(uri), MongoClient(uri, serverSelectionTimeoutMS=75000) as source:
                before = manifest(source[DATABASE])
                require(all(before.get(k, {}).get("count", 0) for k in ("archetypes", "figures", "media")),
                        "Personal production has not been seeded.")
                with pending.open("xb") as file:
                    dump(file, uri, before)
            collections, docs = verify(pending)
            pending.rename(path)
        finally:
            pending.unlink(missing_ok=True)
    print(f"Verified {collections} collections, {docs} documents, content digests and indexes: {path.name}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        # Generic PyMongo/Docker errors can contain topology or credential information.
        sys.exit(str(exc) if type(exc) == RuntimeError else f"Backup command failed ({type(exc).__name__}).")
    except Exception as exc:
        sys.exit(f"Backup command failed ({type(exc).__name__}).")
