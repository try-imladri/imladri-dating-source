"""Measure provider attempts and token usage without retaining member content."""
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic_ns

execution = ContextVar("ai_execution", default="included")
metrics = ContextVar("ai_attempt_metrics", default=None)


async def in_context(mode, function, *args, **kwargs):
    token = execution.set(mode)
    try:
        return await function(*args, **kwargs)
    finally:
        execution.reset(token)


@contextmanager
def provider_attempt():
    row = metrics.get()
    if row is None:
        yield
        return
    row["retries"] += int(row["attempts"] > 0)
    row["attempts"] += 1
    started = monotonic_ns()
    try:
        yield
    finally:
        row["timed_attempts"] += 1
        row["provider_ms"] += (monotonic_ns() - started) // 1_000_000


def token_counts(usage):
    if not isinstance(usage, dict):
        return None
    inputs, outputs = usage.get("prompt_tokens"), usage.get("completion_tokens")
    total = usage.get("total_tokens")
    if (any(type(n) is not int or n < 0 for n in (inputs, outputs))
            or ("total_tokens" in usage and (type(total) is not int or total != inputs + outputs))):
        return None
    return inputs, outputs


def total_only(usage):
    if not isinstance(usage, dict) or "prompt_tokens" in usage or "completion_tokens" in usage:
        return None
    total = usage.get("total_tokens")
    return total if type(total) is int and total >= 0 else None


# USD nanodollars per token at the November 2026 off-peak rates. DeepSeek
# publishes separate cache-hit, cache-miss, and generated-token prices.
OFF_PEAK_RATES = {
    "deepseek-flash": (3, 150, 600),
    "deepseek-v4-flash": (3, 150, 600),
    "deepseek-v4-flash-vision-exp": (3, 150, 600),
    "deepseek-v4.1-flash-1017": (3, 150, 600),
    "deepseek-v4-pro": (22, 660, 1980),
    "deepseek-v4-pro-0813": (22, 660, 1980),
}


def usage_fields(usage, model):
    counts = token_counts(usage)
    if counts is None:
        total = total_only(usage)
        return {"total_tokens": total} if total is not None else {}
    inputs, outputs = counts
    fields = {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs}
    hit, miss = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
    split = all(type(n) is int and n >= 0 for n in (hit, miss)) and hit + miss == inputs
    if split:
        fields.update(cache_hit_tokens=hit, cache_miss_tokens=miss)
    rates = OFF_PEAK_RATES.get(model.lower()) if isinstance(model, str) else None
    if rates:
        cached, fresh, completion = rates
        low = cached * hit + fresh * miss if split else cached * inputs
        high = low if split else fresh * inputs
        fields.update(low_nusd=low + completion * outputs, high_nusd=2 * (high + completion * outputs))
    return fields


def record_usage(usage, model):
    row = metrics.get()
    if row is None:
        return
    fields = usage_fields(usage, model)
    if "input_tokens" in fields:
        fields.pop("total_tokens")
        row.update(fields)
        row["reported"] += 1
        if "cache_hit_tokens" in fields:
            row["cache_reported"] += 1
        if "low_nusd" in fields:
            row["priced"] += 1
    elif "total_tokens" in fields:
        row.update(total_only_reports=1, total_only_tokens=fields["total_tokens"])


def start():
    counters = Counter()
    return counters, metrics.set(counters)
