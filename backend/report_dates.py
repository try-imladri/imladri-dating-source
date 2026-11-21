"""UTC reporting windows for current ISO timestamps and historical BSON dates."""
from datetime import date, datetime, timedelta, timezone


def instant(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except (ValueError, OverflowError):
            return None
        if value.tzinfo is None:
            return None
    if not isinstance(value, datetime):
        return None
    # PyMongo returns BSON UTC dates without tzinfo unless tz_aware is enabled.
    try:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def calendar_ranges(start, end):
    # An offset ISO string can describe a UTC instant on either neighboring day.
    # Keep each branch indexable, then compare the normalized instant.
    text = {"$gte": (start - timedelta(days=1)).isoformat(), "$lt": (end + timedelta(days=1)).isoformat()}
    bson = {"$gte": datetime.combine(start, datetime.min.time(), timezone.utc),
            "$lt": datetime.combine(end, datetime.min.time(), timezone.utc)}
    return text, bson


def mongo_timestamp(field):
    return {"$convert": {"input": {"$cond": [{"$in": [{"$type": field}, ["string", "date"]]}, field, None]},
                         "to": "date", "onError": None, "onNull": None}}


def instant_micros(field):
    """Normalize BSON and offset-aware ISO timestamps to UTC with microsecond precision."""
    def captures(n):
        return {"$arrayElemAt": ["$$iso.captures", n]}
    parsed = {"$regexFind": {"input": field, "regex": r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|[+-]\d\d:\d\d)$"}}
    seconds = {"$convert": {"input": {"$concat": [captures(0), captures(2)]}, "to": "date", "onError": None, "onNull": None}}
    fraction = {"$substrBytes": [{"$concat": [{"$ifNull": [captures(1), ""]}, "000000"]}, 0, 6]}
    return {"$switch": {"branches": [
        {"case": {"$eq": [{"$type": field}, "date"]}, "then": {"$multiply": [{"$toLong": field}, 1000]}},
        {"case": {"$eq": [{"$type": field}, "string"]}, "then": {"$let": {"vars": {"iso": parsed},
            "in": {"$add": [{"$multiply": [{"$toLong": seconds}, 1000]}, {"$toInt": fraction}]}}}},
    ], "default": None}}


def calendar_stages(stamp, dates):
    start, end = date.fromisoformat(dates["$gte"]), date.fromisoformat(dates["$lt"])
    text, bson = calendar_ranges(start, end)
    return [{"$match": {"$or": [{stamp: text}, {stamp: bson}]}},
            {"$set": {"report_time": mongo_timestamp("$" + stamp)}},
            {"$match": {"report_time": bson}}]


def instant_stages(stamp, start, end):
    # A 24-hour window can begin or end between midnights. The broad string
    # bounds cover local dates at either offset extreme before exact filtering.
    text = {"$gte": (start.date() - timedelta(days=1)).isoformat(), "$lt": (end.date() + timedelta(days=2)).isoformat()}
    bounds = {"$gte": start, "$lte": end}
    return [{"$match": {"$or": [{stamp: text}, {stamp: bounds}]}},
            {"$set": {"report_time": mongo_timestamp("$" + stamp)}},
            {"$match": {"report_time": bounds}}]
