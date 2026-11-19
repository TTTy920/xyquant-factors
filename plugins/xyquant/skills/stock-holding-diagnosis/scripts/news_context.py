"""Filter publicly sourced notes; web search and source checks belong to the host."""
from datetime import date
import json
import re
from urllib.parse import parse_qsl, urlsplit


WINDOW_DAYS = 7  # inclusive of the report date
MAX_INPUT = 32 * 1024
SOURCE_ORDER = {"official": 2, "financial_media": 1, "research": 0}


def read_payload(stream):
    raw = stream.read(MAX_INPUT + 1)
    if 0 < len(raw) <= MAX_INPUT:
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                return value
        except (ValueError, RecursionError):
            pass
    return {}  # distinguish invalid input from no search attempt


def text(value, limit):
    return " ".join(value.split()) if isinstance(value, str) and 0 < len(value) <= limit and value.strip() else None


def article_key(link):
    if not isinstance(link, str) or not 0 < len(link) <= 2048 or re.search(r"[\s\x00-\x1f\x7f<>\\]", link):
        return None
    try:
        parsed = urlsplit(link)
        host = parsed.hostname
        if (parsed.scheme not in ("https", "http") or not host or "." not in host
                or parsed.username is not None or parsed.password is not None
                or not (parsed.path.strip("/") or parsed.query)):
            return None
        params = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=50)
        params = tuple(sorted((k, v) for k, v in params
                              if not k.casefold().startswith("utm_") and k.casefold() not in {"gclid", "fbclid"}))
        return (host.removeprefix("www."), parsed.path.rstrip("/"), params)
    except ValueError:
        return None


def recent_news(payload, stock, as_of):
    first = date.fromordinal(max(1, as_of.toordinal() - WINDOW_DAYS + 1))
    result = {
        "status": "not_searched", "from": first.isoformat(), "through": as_of.isoformat(),
        "items": [], "requires_risk_review": False, "used_in_factor_decision": False,
    }
    if payload is None:
        return result
    if not isinstance(payload, dict) or payload.get("stock") != stock:
        return dict(result, status="invalid_input")
    rows = payload.get("items")
    if payload.get("status") == "unavailable" and rows == []:
        return dict(result, status="unavailable")
    if payload.get("status") != "searched" or not isinstance(rows, list) or len(rows) > 12:
        return dict(result, status="invalid_input")

    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind, event, link = row.get("source_type"), row.get("event_key"), row.get("url")
        key = article_key(link)
        if (key is None or not isinstance(kind, str) or kind not in SOURCE_ORDER
                or not isinstance(event, str) or not re.fullmatch(r"[a-z0-9][a-z0-9:_-]{0,95}", event)):
            continue
        fields = {k: text(row.get(k), n) for k, n in (("title", 200), ("source", 90), ("summary", 360))}
        if None in fields.values():
            continue
        try:
            published = date.fromisoformat(row.get("published"))
        except (ValueError, TypeError):
            continue
        if published.isoformat() != row["published"] or not first <= published <= as_of:
            continue
        item = {**fields, "url": link, "published": row["published"], "source_type": kind,
                "material_risk": row.get("material_risk") is True and kind != "research"}
        candidates.append((item, event, key))

    # Keep the most authoritative version of one event, then present the latest distinct events.
    visited_events, visited_links, unique = set(), set(), []
    for item, event, key in sorted(candidates,
                                   key=lambda row: (SOURCE_ORDER[row[0]["source_type"]], row[0]["published"]),
                                   reverse=True):
        if event not in visited_events and key not in visited_links:
            unique.append(item)
        visited_events.add(event)
        visited_links.add(key)
    def recency(item):
        return item["published"], SOURCE_ORDER[item["source_type"]]

    latest = sorted(unique, key=recency, reverse=True)
    risk = next((item for item in latest if item["material_risk"]), None)
    items = [risk] if risk else []
    for item in latest:
        if item is risk or (item["source_type"] == "research" and any(x["source_type"] == "research" for x in items)):
            continue
        if len(items) == 2:
            break
        items.append(item)
    items.sort(key=recency, reverse=True)
    return dict(result, status="found" if items else "no_verified_news", items=items,
                requires_risk_review=any(item["material_risk"] for item in items))
