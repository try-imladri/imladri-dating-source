import base64
import json
import logging
import os
import re

import httpx
from fastapi import HTTPException
from pymongo.errors import PyMongoError

try:
    from . import ai_meter, message_costs, ops
except ImportError:
    import ai_meter, message_costs, ops


DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"


def parse_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    if start < 0 or end < 0:
        raise ValueError("no json")
    return json.loads(text[start:end + 1])


def _image_url(image_b64: str) -> str:
    # The screenshot routes pass raw base64 without the browser's MIME label.
    data = image_b64.split(",", 1)[-1].strip()
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        raise HTTPException(status_code=422, detail="The screenshot could not be read.") from None

    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif raw.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise HTTPException(status_code=422, detail="Use a PNG, JPEG, GIF, or WebP screenshot.")
    return f"data:{mime};base64,{data}"


def _completion_text(envelope):
    try:
        choice = envelope["choices"][0]
        result = choice["message"]["content"]
        if choice["finish_reason"] == "stop" and isinstance(result, str):
            return result
    except (IndexError, KeyError, TypeError):
        pass
    raise HTTPException(status_code=502, detail="The AI service returned an incomplete response. Please try again.")


async def ai_text(system: str, prompt: str, image_b64: str | None = None, *, json_output: bool = False,
                  retry: bool = False) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="Personalized replies are waiting for the AI provider to be connected.")
    identity = message_costs.checked_key(api_key)

    content = prompt
    if image_b64:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": _image_url(image_b64), "detail": "high"}},
        ]
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "thinking": {"type": "disabled"},
        "max_tokens": 8192,
    }
    if json_output:
        payload["response_format"] = {"type": "json_object"}

    budget = ops.request_budget_db.get()
    reservation = None
    if budget is not None:
        reservation = await ops.reserve_tokens(budget, system, prompt, bool(image_b64), payload["max_tokens"], model=MODEL)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
            async with message_costs.provider_call(budget, identity, MODEL, retry) as receipt:
                with ai_meter.provider_attempt():
                    response = await client.post(DEEPSEEK_CHAT_URL, json=payload, headers={"Authorization": f"Bearer {api_key}"})
                receipt.http_status = response.status_code
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        receipt.envelope = body
                except (ValueError, UnicodeError):
                    pass
                if response.status_code != 200:
                    receipt.status = "http_error"
                    if response.status_code in (401, 402, 403, 429):
                        raise HTTPException(status_code=503, detail="The AI service is temporarily unavailable. Please try again.")
                    raise HTTPException(status_code=502, detail="The AI service could not complete this request. Please try again.")
                envelope = receipt.envelope
                ai_meter.record_usage(envelope.get("usage"), envelope.get("model", MODEL))
                if reservation:
                    try:
                        await ops.settle_tokens(budget, reservation, envelope.get("usage"))
                    except PyMongoError:
                        logging.getLogger(__name__).warning("AI budget settlement was not confirmed")
                result = _completion_text(envelope)
                receipt.status = "succeeded"
                return result
    except message_costs.TrackingUnavailable:
        if reservation:
            try:
                await ops.release_undispatched(budget, reservation)
            except PyMongoError:
                logging.getLogger(__name__).warning("Could not release an undispatched AI reservation")
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="The AI service took too long to respond. Please try again.") from None
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="The AI service could not be reached. Please try again.") from None


async def ai_json(system: str, prompt: str, image_b64: str | None = None):
    system += "\n\nRespond with ONLY valid JSON matching the requested shape. No markdown, no commentary."
    for attempt in range(2):
        text = await ai_text(system, prompt, image_b64, json_output=True, retry=attempt > 0)
        try:
            result = parse_json(text)
        except ValueError:
            continue
        if isinstance(result, dict):
            return result
    raise HTTPException(status_code=502, detail="AI returned an unreadable answer, please try again")
