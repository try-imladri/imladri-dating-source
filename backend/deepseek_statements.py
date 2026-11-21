"""Parse and reconcile an official DeepSeek Usage ZIP without retaining credentials or account IDs."""
import csv
import hmac
import io
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

UTC = timezone.utc
TOKEN_TYPES = ("input_cache_hit_tokens", "input_cache_miss_tokens", "output_tokens")
MAX_UPLOAD = 2_000_000
MAX_ROWS = 25000


def reject(message):
    raise HTTPException(422, message)


def model_name(model):
    if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9._-]{1,80}", model):
        return ""
    name = model.lower()
    if name in ("deepseek-v4.1-flash-1017", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"):
        return "deepseek-flash"
    return "deepseek-v4-pro" if name == "deepseek-v4-pro-0813" else name


def nanodollars(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,12}(?:\.\d{1,20})?", value):
        reject("The export contains an invalid USD amount or unit rate.")
    nanos = Decimal(value) * 1_000_000_000
    if nanos != nanos.to_integral_value() or nanos > 2**62:
        reject("The amount exceeds the supported billing precision.")
    return int(nanos)


def matches_mask(raw, credential):
    if raw == credential and raw:
        return True
    parts = re.fullmatch(r"(sk-[A-Za-z0-9_-]{3,})(?:\*+|…+)([A-Za-z0-9_-]{4,})", raw or "")
    return bool(parts and hmac.compare_digest(credential[:len(parts[1])], parts[1])
                and hmac.compare_digest(credential[-len(parts[2]):], parts[2]))


def empty_model():
    return {"requests": 0, "prices": {k: {} for k in TOKEN_TYPES}, "billed": 0, "paid": 0}


def combine_models(target, source):
    for model, row in source.items():
        dest = target.setdefault(model, empty_model())
        for key in ("requests", "billed", "paid"):
            dest[key] += row[key]
        for kind in TOKEN_TYPES:
            for rate, count in row["prices"][kind].items():
                dest_rates = dest["prices"][kind]
                dest_rates[rate] = dest_rates.get(rate, 0) + count


def parse_statement(payload, label, credential, now=None):
    """Return one complete immutable record per closed UTC day represented by this export."""
    current = (now or datetime.now(UTC)).date()
    if not payload or len(payload) > MAX_UPLOAD:
        reject("Choose a DeepSeek Usage ZIP smaller than 2 MB.")
    buckets = defaultdict(dict)
    periods = set()
    seen = set()
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = archive.infolist()
            if len(files) != 2 or sum(i.file_size for i in files) > 8_000_000:
                reject("Choose the original ZIP containing one cost CSV and one amount CSV.")
            names = [re.fullmatch(r"(amount|cost)-(\d{4}-\d\d-\d\d)_(\d{4}-\d\d-\d\d)\.csv", i.filename) for i in files]
            if (not all(names) or {n[1] for n in names} != {"amount", "cost"}
                    or len({(n[2], n[3]) for n in names}) != 1):
                reject("Choose the original DeepSeek Usage export for one date range.")
            first, last = (date.fromisoformat(v) for v in names[0].groups()[1:])
            if not 0 <= (last-first).days <= 92:
                reject("Export at most 93 UTC calendar days at a time.")
            for info, matched in zip(files, names):
                table = matched[1]
                required = {"start_time_iso", "end_time_iso", "model", "utc_date"}
                required.update(("api_key_name", "api_key", "type", "amount", "price") if table == "amount"
                                else ("wallet_type", "cost", "currency"))
                with archive.open(info) as file:
                    reader = csv.DictReader(io.TextIOWrapper(file, encoding="utf-8-sig", newline=""))
                    if not required <= set(reader.fieldnames or []):
                        reject("The export is missing required billing columns.")
                    for line, row in enumerate(reader):
                        if line >= MAX_ROWS:
                            reject("Export a shorter billing period.")
                        start, end = (datetime.fromisoformat(row[k]) for k in ("start_time_iso", "end_time_iso"))
                        if (start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0) or
                                start.minute or start.second or start.microsecond or
                                start.strftime("%Y%m%d") != row["utc_date"] or not first <= start.date() <= last):
                            reject("The export must contain UTC calendar days or whole UTC hours.")
                        delta = end-start
                        if delta == timedelta(days=1) and start.hour == 0:
                            granularity = "daily"
                        elif delta == timedelta(hours=1):
                            granularity = "hourly"
                        else:
                            reject("The export contains an unsupported time bucket.")
                        periods.add(granularity)
                        stamp = start.isoformat()
                        model = model_name(row["model"])
                        if not model:
                            reject("The export contains an invalid model identifier.")
                        item = buckets[stamp].setdefault(model, empty_model())
                        if table == "amount":
                            if row["api_key_name"] != label or not matches_mask(row["api_key"], credential):
                                reject("Export usage filtered to the dedicated production key.")
                            kind = row["type"]
                            if not re.fullmatch(r"\d{1,16}", row["amount"]):
                                reject("The export contains an invalid token or request count.")
                            count = int(row["amount"])
                            if kind == "request_count" and not row["price"]:
                                item["requests"] += count
                                identity = (stamp, model, kind)
                            elif kind in TOKEN_TYPES:
                                rate = str(nanodollars(row["price"]))
                                item["prices"][kind][rate] = count
                                identity = (stamp, model, kind, rate)
                            else:
                                reject("The export includes an unsupported usage type.")
                        else:
                            wallet = row["wallet_type"].lower()
                            if row["currency"] != "USD" or wallet not in ("paid", "granted"):
                                reject("Only USD paid or granted wallet charges are supported.")
                            charge = nanodollars(row["cost"])
                            item["billed"] += charge
                            item["paid"] += charge if wallet == "paid" else 0
                            identity = (stamp, model, wallet)
                        if identity in seen:
                            reject("The export contains duplicate billing rows.")
                        seen.add(identity)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, csv.Error, UnicodeError, ValueError, KeyError, TypeError, OverflowError, InvalidOperation):
        reject("The file could not be read as an official DeepSeek usage export.")
    if len(periods) != 1 or not any((s, m, "request_count") in seen for s in buckets for m in buckets[s]):
        reject("The export needs usage for the dedicated key at a single time granularity.")
    result = {}
    granularity = periods.pop()
    for stamp, models in sorted(buckets.items()):
        for model, row in models.items():
            cost = sum(int(p)*n for rates in row["prices"].values() for p,n in rates.items())
            if (cost != row["billed"] or (stamp, model, "request_count") not in seen or
                    not any((stamp, model, w) in seen for w in ("paid", "granted"))):
                reject("The key's token charges must match the wallet charges for each model and period.")
        day = stamp[:10]
        if day >= current.isoformat():
            continue
        date_data = result.setdefault(day, {"totals": {}, "hours": {}})
        combine_models(date_data["totals"], models)
        if granularity == "hourly":
            date_data["hours"][stamp[11:13]] = models
    if not result:
        reject("Choose a completed UTC day with billed usage.")
    # Daily exports explicitly cover the complete UTC dates named in both CSVs.
    # Hourly exports only establish coverage for the days containing their buckets.
    if granularity == "daily":
        day = first
        while day <= last and day < current:
            result.setdefault(day.isoformat(), {"totals": {}, "hours": {}})
            day += timedelta(days=1)
    return result
