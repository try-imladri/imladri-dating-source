"""Catalog provenance for derived archetype captions, never invented user facts.

Existing model formats contain no source citations, so freeform rationale text
cannot prove a claim about the member. Scores remain tentative AI estimates;
active captions quote the trusted catalog instead. Projections are pure and
keep older text explicitly historical/unverified without modifying user data.
"""
from copy import deepcopy

POLICY = "catalog-comparison-v1"
NEUTRAL = "Tentative comparison with the selected catalog archetype."
HISTORY_NOTE = "Historical unverified AI wording; not a recorded fact about the member."


def _text(value, limit=400):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def basis(archetype):
    archetype = archetype if isinstance(archetype, dict) else {}
    return {"name": _text(archetype.get("name"), 80), "one_liner": _text(archetype.get("one_liner"), 400)}


def catalog_line(archetype, limit=160):
    description = basis(archetype)["one_liner"]
    if not description:
        return NEUTRAL[:limit]
    prefix = "Catalog theme (tentative comparison): “"
    room = max(0, limit - len(prefix) - 1)
    if len(description) > room:
        description = description[:max(0, room - 1)].rstrip() + "…"
    return (prefix + description + "”")[:limit]


def new_caption(archetype, limit=160, raw=None):
    result = {"line": catalog_line(archetype, limit), "line_policy": POLICY, "catalog_basis": basis(archetype)}
    if isinstance(raw, str) and raw.strip() and _text(raw, limit) != result["line"]:
        result["historical_unverified_rationales"] = [{"status": "historical_unverified_AI_text",
            "note": HISTORY_NOTE, "line": _text(raw, limit)}]
    return result


def _retain_previous(target, old, safe, limit=160):
    if isinstance(old, str) and old.strip() and old != safe:
        catalog = target.get("catalog_basis")
        catalog_derived = (target.get("line_policy") == POLICY and isinstance(catalog, dict)
                           and old == catalog_line(catalog, limit))
        field = "historical_catalog_rationales" if catalog_derived else "historical_unverified_rationales"
        previous = target.get(field)
        previous = deepcopy(previous) if isinstance(previous, list) else []
        record = ({"status": "historical_catalog_caption", "line_policy": target["line_policy"],
                   "catalog_basis": deepcopy(catalog), "line": old}
                  if catalog_derived else
                  {"status": "historical_unverified_AI_text", "note": HISTORY_NOTE, "line": old})
        if record not in previous:
            previous.append(record)
        target[field] = previous


def ranking_cache(cache, roster=None):
    """Project a derived cache using an already-loaded catalog; no I/O or mutation."""
    if not isinstance(cache, dict):
        return deepcopy(cache)
    result = deepcopy(cache)
    if not isinstance(cache.get("items"), list):
        return result
    catalog = {item["number"]: item for item in roster or [] if isinstance(item, dict) and "number" in item}
    items = []
    for original in cache["items"]:
        if not isinstance(original, dict):
            items.append(deepcopy(original)); continue
        item = deepcopy(original)
        # A missing/failed model result stays missing/failed. This is a caption
        # projection, not evidence that a model generation completed.
        if isinstance(item.get("line"), str) and item["line"].strip() and item.get("failed") is not True:
            selected = catalog.get(item.get("number"))
            if selected is None:
                selected = item.get("catalog_basis") if item.get("line_policy") == POLICY else None
            safe = new_caption(selected)
            _retain_previous(item, item["line"], safe["line"])
            item.update(safe)
        elif item.get("failed") is True:
            _retain_previous(item, item.get("line"), "")
            item["line"] = ""
        items.append(item)
    result["items"] = items
    return result


def person_match(match):
    """Legacy person captions need no catalog fetch; empty/failed stays so."""
    if not isinstance(match, dict):
        return deepcopy(match)
    result = deepcopy(match)
    old = match.get("line")
    if match.get("failed") is True:
        _retain_previous(result, old, "", 200)
        result["line"] = ""
    elif isinstance(old, str) and old.strip():
        selected = match.get("catalog_basis") if match.get("line_policy") == POLICY else None
        safe = new_caption(selected, 200)
        _retain_previous(result, old, safe["line"], 200)
        result.update(safe)
    return result


def person(document):
    result = deepcopy(document)
    if isinstance(result, dict) and "type_match" in result:
        result["type_match"] = person_match(result["type_match"])
    return result


def account(document):
    result = deepcopy(document)
    about = result.get("about") if isinstance(result, dict) else None
    if isinstance(about, dict):
        for key in ("type_compare", "type_compare_female"):
            if key in about:
                about[key] = ranking_cache(about[key])
    return result


def export_document(document):
    """The account/person export serializers share this pure derived projection."""
    if not isinstance(document, dict):
        return document
    if "about" not in document and "type_match" not in document:
        return document
    return person(account(document))
