"""Resolve only references from the prompt's owner-scoped evidence map."""

def reviewed_messages(exchange):
    entries = exchange.get("conversation")
    if not isinstance(entries, list):
        return ""
    labels = {"me": "Member", "them": "Other person"}
    excerpts = []
    for row in entries[-6:]:
        if isinstance(row, dict) and row.get("speaker") in labels and isinstance(row.get("text"), str):
            excerpts.append(labels[row["speaker"]] + ": " + row["text"][:180])
    return "\n".join(excerpts)


def references(ids, evidence):
    if not isinstance(ids, list):
        return []
    valid = dict.fromkeys(k for k in ids if isinstance(k, str) and k in evidence)
    return [evidence[k] for k in list(valid)[:8]]


def apply(report, output, evidence):
    for item in [*report["sections"], *report["traits"]]:
        refs = references(item.pop("source_ids", None), evidence)
        item["evidence_refs"] = refs
        item.pop("sources", None)
        if not refs and item.get("read"):
            item.update(read=None, confidence=None, empty_note="No supporting saved example was linked.")
    report["traits"] = [item for item in report["traits"] if item["evidence_refs"]]
    for field in ("change", "interpretation"):
        report[field + "_evidence"] = references(output.get(field + "_source_ids"), evidence)
        if not report[field + "_evidence"]:
            report[field + "_note"] = None
    c = report["compatibility"]
    if not c:
        return
    # Keep legacy string arrays for exports and older readers.
    c["evidence_refs"] = references(c.pop("source_ids", None), evidence)
    by_claim = {}
    for field in ("summary", "tip"):
        by_claim[field] = references(c.pop(field + "_source_ids", None), evidence)
        if not by_claim[field]:
            c[field] = None
    for field in ("works", "friction"):
        words, refs = [], []
        for item in (c.get(field) or []) if isinstance(c.get(field), list) else []:
            value = item.get("text") if isinstance(item, dict) else item
            linked = references(item.get("source_ids"), evidence) if isinstance(item, dict) else []
            if linked and isinstance(value, str) and value.strip():
                words.append(value)
                refs.append(linked)
        by_claim[field], c[field] = refs, words
    c["claim_evidence"] = by_claim
    if not any(c.get(field) for field in ("summary", "tip", "works", "friction")):
        report["compatibility"] = None
