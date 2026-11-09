"""Small client for the two Naver Open APIs the pipeline uses.

Credentials: in the cloud environment the network proxy adds the
X-Naver-Client-Id / X-Naver-Client-Secret headers for openapi.naver.com.
When running somewhere else, set NAVER_CLIENT_ID and NAVER_CLIENT_SECRET.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://openapi.naver.com"
TIMEOUT = 20


class NaverApiError(RuntimeError):
    pass


def _headers(extra=None):
    headers = {"User-Agent": "naver-blog-pipeline/1.0"}
    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if client_id:
        headers["X-Naver-Client-Id"] = client_id
    if client_secret:
        headers["X-Naver-Client-Secret"] = client_secret
    if extra:
        headers.update(extra)
    return headers


def _request(method, path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    extra = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        extra = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=_headers(extra))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise NaverApiError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NaverApiError(f"{method} {path} -> {e}") from e


def datalab_search(start_date, end_date, groups, time_unit="date"):
    """DataLab search-trend API.

    groups: list of (group_name, [keywords]) — at most 5 per call.
    Returns {group_name: {period: ratio}}. Ratios are relative to the
    largest value inside this one call (0-100).
    """
    if not 1 <= len(groups) <= 5:
        raise ValueError("DataLab accepts 1-5 keyword groups per call")
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "timeUnit": time_unit,
        "keywordGroups": [{"groupName": name, "keywords": kws[:20]} for name, kws in groups],
    }
    payload = _request("POST", "/v1/datalab/search", body=body)
    out = {}
    for result in payload.get("results", []):
        out[result["title"]] = {row["period"]: float(row["ratio"]) for row in result.get("data", [])}
    return out


_TAG = re.compile(r"<[^>]+>")


def blog_search(query, display=10, sort="sim"):
    """Blog search API. Returns {"total": int, "items": [{title, link, postdate, blogger}]}."""
    payload = _request("GET", "/v1/search/blog.json", params={"query": query, "display": display, "sort": sort})
    items = []
    for it in payload.get("items", []):
        items.append({
            "title": _TAG.sub("", it.get("title", "")),
            "link": it.get("link", ""),
            "postdate": it.get("postdate", ""),
            "blogger": it.get("bloggername", ""),
        })
    return {"total": int(payload.get("total", 0)), "items": items}
