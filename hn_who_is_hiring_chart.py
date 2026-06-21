#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import time
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

API_URL = "https://hn.algolia.com/api/v1/search_by_date"
SEARCH_URL = "https://hn.algolia.com/api/v1/search"
DEFAULT_QUERY = "Ask HN: Who is hiring?"

CATEGORY_RULES: List[tuple[str, List[str]]] = [
    ("Crypto/Web3", [
        r"\bblockchain\b", r"\bweb3\b", r"\bdefi\b", r"\bnft\b",
        r"\bcrypto-?currency\b", r"\btoken(s)?\b", r"\bstablecoin(s)?\b",
        r"\bsmart contract(s)?\b", r"\bsolidity\b", r"\beth(ereum)?\b",
        r"\bbitcoin\b", r"\bbtc\b", r"\bcoin(s)?\b", r"\bon-?chain\b",
        r"\bwallet(s)?\b", r"\bexchange(s)?\b"
    ]),
    ("AI/ML", ["machine learning", r"\bml\b", "deep learning", r"\bllm\b", "nlp", "computer vision", r"\bcv\b", "gen ai", "generative", "transformer", "diffusion", r"\bai\b"]),
    ("Hardware/Robotics", ["hardware", "firmware", "embedded", r"\biot\b", "robot", "robotics", "fpga", "pcb", "sensor", "mechatronics", "autonomous", "drone"]),
    ("Security", ["security", "infosec", "secops", r"\bsoc\b", "threat", "vulnerability", "pentest", "penetration", "zero trust", r"\biam\b"]),
    ("Fintech", ["fintech", "payments", "payment", "card", "banking", "lending", "mortgage", "insurtech", "trading", "brokerage"]),
    ("Health/Biotech", ["health", "healthcare", "medtech", "biotech", "clinical", "pharma", "genomics", "lab"]),
    ("Climate/Energy", ["climate", "energy", "renewable", "solar", "wind", "battery", "grid", "carbon", "sustainability"]),
    ("Gaming", ["game", "gaming", "unity", "unreal"]),
    ("Mobile", [r"\bios\b", "android", "mobile", "react native", "swift", "kotlin"]),
    ("Data/Analytics", ["data engineer", "data science", "analytics", r"\betl\b", "warehouse", r"\bbi\b", r"\bsql\b", "spark", "big data"]),
    ("Devtools/Infra", ["devops", r"\bsre\b", "platform", "kubernetes", r"\bk8s\b", "cloud", r"\baws\b", r"\bgcp\b", r"\bazure\b", "infra", "observability", "ci/cd", "terraform"]),
]
CATEGORY_ORDER = [name for name, _ in CATEGORY_RULES] + ["Other"]
CATEGORY_CACHE_VERSION = 4
SUPPORTED_CATEGORY_CACHE_VERSIONS = {3, CATEGORY_CACHE_VERSION}
LEGACY_CATEGORY_ALIASES = {
    "Cryptography": "Other",
}


def fetch_page(
    query: str,
    page: int,
    hits_per_page: int,
    timeout: int,
    ssl_context: Optional[ssl.SSLContext],
) -> Dict[str, Any]:
    params = {
        "query": query,
        "tags": "story",
        "page": page,
        "hitsPerPage": hits_per_page,
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "hn-who-is-hiring-chart/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
        return json.load(resp)


def fetch_all(
    query: str,
    hits_per_page: int,
    delay_s: float,
    timeout: int,
    ssl_context: Optional[ssl.SSLContext],
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    page = 0
    hits: List[Dict[str, Any]] = []
    nb_pages: Optional[int] = None
    while True:
        data = fetch_page(
            query=query,
            page=page,
            hits_per_page=hits_per_page,
            timeout=timeout,
            ssl_context=ssl_context,
        )
        hits.extend(data.get("hits", []))
        if nb_pages is None:
            nb_pages = data.get("nbPages", 0)
        page += 1
        if max_pages is not None and page >= max_pages:
            break
        if nb_pages is not None and page >= nb_pages:
            break
        time.sleep(delay_s)
    return hits


def clean_text(text: str) -> str:
    stripped = re.sub(r"<[^>]+>", " ", text)
    unescaped = html.unescape(stripped)
    return re.sub(r"\s+", " ", unescaped).strip().lower()


def compile_category_rules() -> List[tuple[str, List[re.Pattern[str]]]]:
    compiled: List[tuple[str, List[re.Pattern[str]]]] = []
    for name, patterns in CATEGORY_RULES:
        compiled.append((name, [re.compile(p) for p in patterns]))
    return compiled


def classify_comment(text: str, rules: List[tuple[str, List[re.Pattern[str]]]]) -> str:
    if not text:
        return "Other"
    cleaned = clean_text(text)
    if not cleaned:
        return "Other"
    for name, patterns in rules:
        for pattern in patterns:
            if pattern.search(cleaned):
                return name
    return "Other"


def normalize_category_counts(counts: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {name: 0 for name in CATEGORY_ORDER}
    for category, value in (counts or {}).items():
        target = LEGACY_CATEGORY_ALIASES.get(category, category)
        if target not in normalized:
            target = "Other"
        normalized[target] += value
    return normalized


def category_order_for_latest_share(rows: List[Dict[str, Any]]) -> List[str]:
    latest_categories: Dict[str, Any] = {}
    for row in reversed(rows):
        categories = row.get("categories")
        if categories:
            latest_categories = normalize_category_counts(categories)
            break
    if not latest_categories:
        return list(CATEGORY_ORDER)

    base_index = {category: index for index, category in enumerate(CATEGORY_ORDER)}
    ordered = [category for category in CATEGORY_ORDER if category != "Other"]
    ordered.sort(
        key=lambda category: (-latest_categories.get(category, 0), base_index[category])
    )
    if "Other" in CATEGORY_ORDER:
        ordered.append("Other")
    return ordered


def normalize_category_cache(category_cache: Dict[str, Any]) -> bool:
    changed = False
    for story_id, entry in list(category_cache.items()):
        if story_id == "_meta" or not isinstance(entry, dict):
            continue
        counts = entry.get("counts")
        if not isinstance(counts, dict):
            continue
        normalized = normalize_category_counts(counts)
        if normalized != counts:
            entry["counts"] = normalized
            changed = True
    meta = category_cache.get("_meta")
    if not isinstance(meta, dict) or meta.get("version") != CATEGORY_CACHE_VERSION:
        category_cache["_meta"] = {
            "version": CATEGORY_CACHE_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        changed = True
    return changed


def month_bounds(month: str) -> tuple[int, int]:
    year, month_num = (int(part) for part in month.split("-"))
    start_dt = datetime(year, month_num, 1, tzinfo=timezone.utc)
    if month_num == 12:
        end_dt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_dt = datetime(year, month_num + 1, 1, tzinfo=timezone.utc)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def fetch_comment_count(
    start_ts: int,
    end_ts: int,
    timeout: int,
    ssl_context: Optional[ssl.SSLContext],
    retries: int,
    retry_delay: float,
) -> tuple[int, bool]:
    params = {
        "query": "",
        "tags": "comment",
        "numericFilters": f"created_at_i>={start_ts},created_at_i<{end_ts}",
        "hitsPerPage": 0,
    }
    url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "hn-who-is-hiring-chart/1.0"}
    )
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
                data = json.load(resp)
            return int(data.get("nbHits", 0)), bool(data.get("exhaustiveNbHits", False))
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(retry_delay * (2 ** attempt))
            else:
                raise
    raise RuntimeError("Unreachable") from last_error


def fetch_comments_page(
    story_id: str,
    page: int,
    hits_per_page: int,
    timeout: int,
    ssl_context: Optional[ssl.SSLContext],
) -> Dict[str, Any]:
    params = {
        "tags": f"comment,story_{story_id}",
        "page": page,
        "hitsPerPage": hits_per_page,
    }
    url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "hn-who-is-hiring-chart/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
        return json.load(resp)


def fetch_story_category_counts(
    story_id: str,
    delay_s: float,
    timeout: int,
    ssl_context: Optional[ssl.SSLContext],
    hits_per_page: int,
    rules: List[tuple[str, List[re.Pattern[str]]]],
) -> Dict[str, int]:
    counts: Dict[str, int] = {name: 0 for name in CATEGORY_ORDER}
    page = 0
    nb_pages: Optional[int] = None
    while True:
        data = fetch_comments_page(
            story_id=story_id,
            page=page,
            hits_per_page=hits_per_page,
            timeout=timeout,
            ssl_context=ssl_context,
        )
        hits = data.get("hits", [])
        for hit in hits:
            parent_id = hit.get("parent_id")
            if parent_id is None or str(parent_id) != str(story_id):
                continue
            text = hit.get("comment_text") or ""
            if not text:
                continue
            category = classify_comment(text, rules)
            counts[category] = counts.get(category, 0) + 1
        if nb_pages is None:
            nb_pages = data.get("nbPages", 0)
        page += 1
        if nb_pages is not None and page >= nb_pages:
            break
        time.sleep(delay_s)
    return counts


def fetch_monthly_hn_comment_total(
    month: str,
    delay_s: float,
    timeout: int,
    ssl_context: Optional[ssl.SSLContext],
    bucket_days: int,
    retries: int,
    retry_delay: float,
) -> int:
    start_ts, end_ts = month_bounds(month)
    total = 0
    current = start_ts
    bucket_seconds = max(1, bucket_days) * 86400
    while current < end_ts:
        next_ts = min(end_ts, current + bucket_seconds)
        count, exhaustive = fetch_comment_count(
            start_ts=current,
            end_ts=next_ts,
            timeout=timeout,
            ssl_context=ssl_context,
            retries=retries,
            retry_delay=retry_delay,
        )
        if not exhaustive and (next_ts - current) > 86400:
            day = current
            while day < next_ts:
                day_end = min(next_ts, day + 86400)
                day_count, _ = fetch_comment_count(
                    start_ts=day,
                    end_ts=day_end,
                    timeout=timeout,
                    ssl_context=ssl_context,
                    retries=retries,
                    retry_delay=retry_delay,
                )
                total += day_count
                day = day_end
                time.sleep(delay_s)
        else:
            total += count
        current = next_ts
        time.sleep(delay_s)
    return total


def is_who_is_hiring(title: str) -> bool:
    return title.strip().lower().startswith("ask hn: who is hiring?")


def pick_monthly_posts(hits: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_month: Dict[str, Dict[str, Any]] = {}
    duplicates: List[Dict[str, Any]] = []
    for hit in hits:
        title = (hit.get("title") or "").strip()
        if not title or not is_who_is_hiring(title):
            continue
        created_at = hit.get("created_at")
        if not created_at:
            continue
        month = created_at[:7]
        comments = hit.get("num_comments") or 0
        entry = {
            "month": month,
            "comments": int(comments),
            "object_id": hit.get("objectID"),
            "created_at": created_at,
            "title": title,
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
        }
        prev = by_month.get(month)
        if prev is None or entry["comments"] > prev["comments"]:
            if prev is not None:
                duplicates.append(prev)
            by_month[month] = entry
        else:
            duplicates.append(entry)
    rows = sorted(by_month.values(), key=lambda r: r["month"])
    return rows, duplicates


def write_csv(rows: List[Dict[str, Any]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "month",
                "comments",
                "hn_total_comments",
                "comments_per_10k",
                "object_id",
                "created_at",
                "title",
                "url",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r["month"],
                    r["comments"],
                    r.get("hn_total_comments"),
                    r.get("comments_per_10k"),
                    r["object_id"],
                    r["created_at"],
                    r["title"],
                    r["url"],
                ]
            )


def write_category_csv(
    rows: List[Dict[str, Any]],
    path: str,
    normalized: bool = False,
) -> None:
    field = "categories_per_10k" if normalized else "categories"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["month", "category", "value"])
        for r in rows:
            categories = r.get(field) or {}
            for category in CATEGORY_ORDER:
                if category in categories:
                    writer.writerow([r["month"], category, categories[category]])


def build_html(rows: List[Dict[str, Any]], generated_at: str) -> str:
    data = [
        {
            "month": r["month"],
            "comments": r["comments"],
            "hn_total_comments": r.get("hn_total_comments"),
            "comments_per_10k": r.get("comments_per_10k"),
            "categories": normalize_category_counts(r.get("categories") or {})
            if r.get("categories")
            else None,
            "categories_per_10k": normalize_category_counts(
                r.get("categories_per_10k") or {}
            )
            if r.get("categories_per_10k")
            else None,
        }
        for r in rows
    ]
    data_json = json.dumps(data)
    categories_json = json.dumps(
        category_order_for_latest_share(rows) if any(r.get("categories") for r in rows) else []
    )
    rules_html = "\n".join(
        f"<li><strong>{html.escape(name)}</strong>: "
        + ", ".join(f"<code>{html.escape(pat)}</code>" for pat in patterns)
        + "</li>"
        for name, patterns in CATEGORY_RULES
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>HN Who Is Hiring? Comments</title>
  <script src="https://cdn.plot.ly/plotly-2.30.0.min.js"></script>
  <style>
    :root {{
      color-scheme: light;
      --bg: oklch(0.975 0.008 96);
      --surface: oklch(0.995 0.005 96);
      --surface-raised: oklch(0.985 0.008 96);
      --text: oklch(0.24 0.025 255);
      --muted: oklch(0.48 0.028 255);
      --line: oklch(0.88 0.018 92);
      --line-strong: oklch(0.78 0.025 92);
      --accent: oklch(0.56 0.16 252);
      --accent-soft: oklch(0.94 0.035 252);
      --focus: oklch(0.68 0.16 252);
      --shadow: 0 18px 48px rgba(51, 43, 31, 0.10);
      --control-shadow: 0 1px 2px rgba(51, 43, 31, 0.08);
    }}
    html, body {{
      height: 100%;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      margin: 0;
      color: var(--text);
      background: var(--bg);
      letter-spacing: 0;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 22px;
      line-height: 1.15;
      letter-spacing: 0;
      font-weight: 720;
    }}
    .page {{
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      padding: 28px;
      box-sizing: border-box;
      gap: 16px;
    }}
    .header {{
      display: grid;
      grid-template-columns: minmax(260px, 1fr) auto;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }}
    .title-block {{
      min-width: 240px;
    }}
    .meta {{
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }}
    .toolbar {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
    }}
    button {{
      font: inherit;
      letter-spacing: 0;
    }}
    .mode-button,
    .theme-toggle {{
      min-height: 34px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      padding: 0 12px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 620;
      box-shadow: var(--control-shadow);
      transition: background-color 180ms cubic-bezier(0.23, 1, 0.32, 1),
        border-color 180ms cubic-bezier(0.23, 1, 0.32, 1),
        color 180ms cubic-bezier(0.23, 1, 0.32, 1),
        transform 120ms cubic-bezier(0.23, 1, 0.32, 1);
    }}
    .mode-button:hover,
    .theme-toggle:hover {{
      border-color: var(--line-strong);
      background: var(--surface-raised);
    }}
    .mode-button:active,
    .theme-toggle:active,
    .tab-button:active {{
      transform: scale(0.98);
    }}
    .mode-button:focus-visible,
    .theme-toggle:focus-visible,
    .tab-button:focus-visible,
    .methodology summary:focus-visible {{
      outline: 2px solid var(--focus);
      outline-offset: 2px;
    }}
    .tab-bar {{
      display: inline-flex;
      gap: 2px;
      align-items: center;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-raised);
    }}
    .tab-button {{
      min-height: 28px;
      border: 0;
      background: transparent;
      color: var(--muted);
      padding: 0 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 620;
      transition: background-color 180ms cubic-bezier(0.23, 1, 0.32, 1),
        color 180ms cubic-bezier(0.23, 1, 0.32, 1),
        transform 120ms cubic-bezier(0.23, 1, 0.32, 1);
    }}
    .tab-button:hover {{
      color: var(--text);
    }}
    .tab-button.active {{
      background: var(--surface);
      color: var(--text);
      box-shadow: var(--control-shadow);
    }}
    .chart-panel {{
      flex: 1 1 auto;
      min-height: 72vh;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 10px;
      overflow: hidden;
    }}
    .chart-panel.hidden {{
      display: none;
    }}
    #chart {{
      width: 100%;
      height: 100%;
      background: transparent;
    }}
    #categoryChart {{
      width: 100%;
      height: 100%;
      background: transparent;
    }}
    .theme-toggle {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}
    .theme-toggle svg {{ width: 16px; height: 16px; }}
    .methodology {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
    }}
    .methodology summary {{
      cursor: pointer;
      font-weight: 600;
      color: var(--text);
    }}
    .methodology p, .methodology li {{
      color: var(--muted);
      line-height: 1.5;
      max-width: 78ch;
    }}
    .methodology code {{
      color: var(--text);
      background: var(--surface-raised);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 1px 4px;
    }}
    body[data-theme="dark"] {{
      color-scheme: dark;
      --bg: oklch(0.18 0.02 258);
      --surface: oklch(0.235 0.02 258);
      --surface-raised: oklch(0.28 0.02 258);
      --text: oklch(0.92 0.012 258);
      --muted: oklch(0.72 0.02 258);
      --line: oklch(0.38 0.028 258);
      --line-strong: oklch(0.50 0.035 258);
      --accent: oklch(0.74 0.12 220);
      --accent-soft: oklch(0.31 0.045 220);
      --focus: oklch(0.76 0.13 220);
      --shadow: 0 20px 56px rgba(4, 10, 24, 0.42);
      --control-shadow: 0 1px 1px rgba(4, 10, 24, 0.32);
    }}
    @media (max-width: 760px) {{
      .page {{
        padding: 16px;
        gap: 12px;
      }}
      .header {{
        grid-template-columns: 1fr;
        align-items: start;
      }}
      .toolbar {{
        justify-content: flex-start;
      }}
      .chart-panel {{
        min-height: 66vh;
        padding: 6px;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
  <div class="header">
    <div class="title-block">
      <h1>HN: Who is hiring? comments per month</h1>
      <p class="meta">Data fetched from HN Algolia API on {generated_at} (UTC).</p>
    </div>
    <div class="toolbar" aria-label="Chart controls">
      <div class="tab-bar" role="tablist" aria-label="Chart view">
        <button class="tab-button active" data-tab="main" type="button">WIH comments</button>
        <button class="tab-button" data-tab="categories" type="button">Category share</button>
      </div>
      <button id="modeToggle" class="mode-button" type="button">Normalize</button>
      <button id="themeToggle" class="theme-toggle" type="button" aria-label="Toggle theme">
        <svg id="themeIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
          <path d="M12 3a1 1 0 0 1 1 1v1.5a1 1 0 1 1-2 0V4a1 1 0 0 1 1-1Z"/>
          <path d="M12 17a5 5 0 1 0 0-10a5 5 0 0 0 0 10Z"/>
          <path d="M4.22 4.22a1 1 0 0 1 1.42 0l1.06 1.06a1 1 0 0 1-1.42 1.42L4.22 5.64a1 1 0 0 1 0-1.42Z"/>
          <path d="M18.36 18.36a1 1 0 0 1 1.42 0l1.06 1.06a1 1 0 1 1-1.42 1.42l-1.06-1.06a1 1 0 0 1 0-1.42Z"/>
          <path d="M3 12a1 1 0 0 1 1-1h1.5a1 1 0 1 1 0 2H4a1 1 0 0 1-1-1Z"/>
          <path d="M17.5 12a1 1 0 0 1 1-1H20a1 1 0 1 1 0 2h-1.5a1 1 0 0 1-1-1Z"/>
          <path d="M4.22 19.78a1 1 0 0 1 0-1.42l1.06-1.06a1 1 0 1 1 1.42 1.42l-1.06 1.06a1 1 0 0 1-1.42 0Z"/>
          <path d="M18.36 5.64a1 1 0 0 1 0-1.42l1.06-1.06a1 1 0 1 1 1.42 1.42l-1.06 1.06a1 1 0 0 1-1.42 0Z"/>
        </svg>
        <span id="themeLabel">Light</span>
      </button>
    </div>
  </div>
  <div class="chart-panel" id="mainPanel">
    <div id="chart"></div>
  </div>
  <div class="chart-panel hidden" id="categoryPanel">
    <div id="categoryChart"></div>
  </div>
  <details class="methodology" open>
    <summary>Methodology & definitions</summary>
    <p><strong>WIH comments</strong> are the comment counts on the monthly “Ask HN: Who is hiring?” post.</p>
    <p><strong>HN comments</strong> are all comments across Hacker News in that month (any post), estimated via the HN Algolia Search API.</p>
    <p><strong>Normalize</strong> means: <em>WIH comments ÷ total HN comments</em>, shown as “comments per 10k HN comments” to make month‑to‑month comparisons fairer.</p>
    <p><strong>Categories</strong> are assigned by keyword matching on each job comment. Each comment is assigned to the first matching bucket, otherwise “Other.”</p>
    <p><strong>Other</strong> includes comments that do not match a named category, including cryptography-specific terms.</p>
    <ul>
      <li>We select one “Who is hiring?” post per month (highest comment count if duplicates).</li>
      <li>We drop the first and last months to avoid partial months.</li>
      <li>Monthly HN comment totals are computed by summing 2‑day buckets, and falling back to 1‑day buckets when Algolia marks a result as non‑exhaustive.</li>
      <li>The smoothing line is a 12‑month moving average over the selected series (raw or normalized).</li>
      <li>The category chart is a 100% stacked area (each month sums to 100%).</li>
    </ul>
  </details>
  <details class="methodology">
    <summary>Category keywords (replication)</summary>
    <p>These are the exact regex/keywords used to assign categories, in order:</p>
    <ul>
      {rules_html}
    </ul>
  </details>
  <script>
    const data = {data_json};
    const categoryOrder = {categories_json};
    const x = data.map(d => d.month + "-01");
    const yRaw = data.map(d => d.comments);
    const yNorm = data.map(d => d.comments_per_10k ?? null);
    const hnTotals = data.map(d => d.hn_total_comments ?? null);
    const normCustom = data.map(d => [d.comments ?? null, d.hn_total_comments ?? null]);
    const categoryColors = [
      "#2563eb", "#0891b2", "#16a34a", "#dc2626", "#7c3aed",
      "#0f766e", "#ca8a04", "#db2777", "#9333ea", "#059669",
      "#475569", "#78716c"
    ];

    const plotTokens = {{
      light: {{
        axis: "#b9b0a3",
        grid: "#e8e1d6",
        hoverBg: "#fbfaf7",
        hoverBorder: "#d7cec0",
        line: "#2563eb",
        marker: "#1d4ed8",
        markerStroke: "#fbfaf7",
        muted: "#6b6258",
        plotBg: "#fbfaf7",
        rangeBg: "#f3eee7",
        text: "#27231f",
        trend: "#a16207",
        yearLine: "#ded6ca"
      }},
      dark: {{
        axis: "#596679",
        grid: "#354256",
        hoverBg: "#202a39",
        hoverBorder: "#4c5a70",
        line: "#38bdf8",
        marker: "#7dd3fc",
        markerStroke: "#202a39",
        muted: "#a7b1c2",
        plotBg: "#202a39",
        rangeBg: "#273244",
        text: "#edf2f7",
        trend: "#facc15",
        yearLine: "#313d50"
      }}
    }};

    function getPlotTokens(theme) {{
      return plotTokens[theme] || plotTokens.light;
    }}

    const baseLayout = {{
      margin: {{ t: 18, r: 24, l: 68, b: 54 }},
      hovermode: "x unified",
      legend: {{ orientation: "h", x: 0, y: 1.1, xanchor: "left" }}
    }};

    const lightTheme = {{
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: plotTokens.light.plotBg,
      font: {{
        color: plotTokens.light.text,
        family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
        size: 12
      }},
      hoverlabel: {{
        bgcolor: plotTokens.light.hoverBg,
        bordercolor: plotTokens.light.hoverBorder,
        font: {{ color: plotTokens.light.text }}
      }}
    }};
    const darkTheme = {{
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: plotTokens.dark.plotBg,
      font: {{
        color: plotTokens.dark.text,
        family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
        size: 12
      }},
      hoverlabel: {{
        bgcolor: plotTokens.dark.hoverBg,
        bordercolor: plotTokens.dark.hoverBorder,
        font: {{ color: plotTokens.dark.text }}
      }}
    }};

    function buildXAxis(theme, width) {{
      const tokens = getPlotTokens(theme);
      const compact = width < 760;
      return {{
        type: "date",
        tickformat: "%Y",
        tickangle: 0,
        dtick: compact ? "M24" : "M12",
        showgrid: false,
        showline: true,
        linecolor: tokens.axis,
        ticks: "outside",
        ticklen: 6,
        tickcolor: tokens.axis,
        tickfont: {{ color: tokens.muted, size: compact ? 11 : 12 }},
        automargin: true,
        rangeslider: {{
          visible: true,
          thickness: compact ? 0.065 : 0.055,
          bgcolor: tokens.rangeBg,
          bordercolor: tokens.axis,
          borderwidth: 1
        }}
      }};
    }}

    function buildYAxis(theme, title, tickformat, range) {{
      const tokens = getPlotTokens(theme);
      const axis = {{
        title: {{ text: title, standoff: 14 }},
        tickformat,
        gridcolor: tokens.grid,
        zeroline: false,
        automargin: true,
        rangemode: "tozero"
      }};
      if (range) {{
        axis.range = range;
        delete axis.rangemode;
      }}
      return axis;
    }}

    function computeStats(values) {{
      const valid = values
        .map((v, i) => (v === null || v === undefined ? null : {{ v, i }}))
        .filter(Boolean);
      if (valid.length === 0) {{
        return {{ maxIdx: -1, minIdx: -1, lastIdx: -1, avg: 0 }};
      }}
      const maxPoint = valid.reduce((a, b) => (b.v > a.v ? b : a));
      const minPoint = valid.reduce((a, b) => (b.v < a.v ? b : a));
      const lastPoint = valid[valid.length - 1];
      const avg = valid.reduce((sum, p) => sum + p.v, 0) / valid.length;
      return {{ maxIdx: maxPoint.i, minIdx: minPoint.i, lastIdx: lastPoint.i, avg }};
    }}

    function computeMovingAverage(values, window) {{
      const out = [];
      for (let i = 0; i < values.length; i++) {{
        const start = Math.max(0, i - window + 1);
        const slice = values.slice(start, i + 1).filter(v => v !== null && v !== undefined);
        if (slice.length === 0) {{
          out.push(null);
        }} else {{
          out.push(slice.reduce((a, b) => a + b, 0) / slice.length);
        }}
      }}
      return out;
    }}

    function formatValue(value, mode) {{
      if (value === null || value === undefined) return "n/a";
      return Math.round(value).toLocaleString();
    }}

    function buildYearLines(theme) {{
      const color = getPlotTokens(theme).yearLine;
      return x
        .map((d) =>
          d.slice(5, 7) === "01" ? {{
            type: "line",
            xref: "x",
            yref: "paper",
            x0: d,
            x1: d,
            y0: 0,
            y1: 1,
            line: {{ color, width: 1, dash: "dot" }}
          }} : null
        )
        .filter(Boolean);
    }}

    function buildCategoryTraces(mode) {{
      const isNorm = mode === "normalized";
      return categoryOrder
        .map((category, idx) => {{
          const y = data.map(d => {{
            const map = d.categories;
            return map && category in map ? map[category] : null;
          }});
          if (y.every(v => v === null)) return null;
          const color = categoryColors[idx % categoryColors.length];
          return {{
            x, y,
            type: "scatter",
            mode: "lines",
            stackgroup: "one",
            groupnorm: "percent",
            fill: "tonexty",
            line: {{ color, width: 1 }},
            fillcolor: color,
            opacity: 0.78,
            customdata: y,
            hovertemplate: "%{{x|%Y-%m}}<br>" + category + ": %{{y:.0f}}%<br>Comments: %{{customdata}}<extra></extra>",
            name: category
          }};
        }})
        .filter(Boolean);
    }}

    function buildTraces(theme, mode) {{
      const isNorm = mode === "normalized";
      const y = isNorm ? yNorm : yRaw;
      const trend = computeMovingAverage(y, 12);
      const stats = computeStats(y);
      const tokens = getPlotTokens(theme);
      const hoverValue = isNorm ? "%{{y:.0f}}" : "%{{y:,}}";
      const trendValue = isNorm ? "%{{y:.0f}}" : "%{{y:,.0f}}";
      return [
        {{
          x, y,
          type: "scatter",
          mode: "lines+markers",
          line: {{ color: tokens.line, width: 2.4 }},
          marker: {{
            color: tokens.marker,
            size: 6,
            line: {{ color: tokens.markerStroke, width: 1.4 }}
          }},
          customdata: isNorm ? normCustom : hnTotals,
          hovertemplate: isNorm
            ? "%{{x|%Y-%m}}<br>Comments/10k: " + hoverValue + "<br>WIH comments: %{{customdata[0]:,}}<br>HN comments: %{{customdata[1]:,}}<extra></extra>"
            : "%{{x|%Y-%m}}<br>Comments: " + hoverValue + "<br>HN comments: %{{customdata:,}}<extra></extra>",
          name: "Comments"
        }},
        {{
          x, y: trend,
          type: "scatter",
          mode: "lines",
          line: {{ color: tokens.trend, width: 2.2, dash: "solid" }},
          hovertemplate: "%{{x|%Y-%m}}<br>Trend: " + trendValue + "<extra></extra>",
          name: "12‑mo MA"
        }},
        {{
          x: stats.maxIdx >= 0 ? [x[stats.maxIdx]] : [],
          y: stats.maxIdx >= 0 ? [y[stats.maxIdx]] : [],
          type: "scatter",
          mode: "markers",
          marker: {{ color: "#16a34a", size: 9, line: {{ color: tokens.markerStroke, width: 1.5 }} }},
          name: "Max"
        }},
        {{
          x: stats.minIdx >= 0 ? [x[stats.minIdx]] : [],
          y: stats.minIdx >= 0 ? [y[stats.minIdx]] : [],
          type: "scatter",
          mode: "markers",
          marker: {{ color: "#dc2626", size: 9, line: {{ color: tokens.markerStroke, width: 1.5 }} }},
          name: "Min"
        }},
        {{
          x: stats.lastIdx >= 0 ? [x[stats.lastIdx]] : [],
          y: stats.lastIdx >= 0 ? [y[stats.lastIdx]] : [],
          type: "scatter",
          mode: "markers",
          marker: {{ color: "#ca8a04", size: 9, line: {{ color: tokens.markerStroke, width: 1.5 }} }},
          name: "Latest"
        }}
      ];
    }}

    function buildCategoryLayout(theme, mode, width) {{
      const isNorm = mode === "normalized";
      const themeLayout = theme === "dark" ? darkTheme : lightTheme;
      const tokens = getPlotTokens(theme);
      return {{
        ...baseLayout,
        ...themeLayout,
        yaxis: buildYAxis(theme, "Share of WIH comments (%)", ",.0f", [0, 100]),
        xaxis: buildXAxis(theme, width),
        legend: {{
          orientation: "h",
          x: 0,
          y: 1.12,
          xanchor: "left",
          font: {{ color: tokens.muted, size: 11 }}
        }},
        hovermode: "x unified"
      }};
    }}

    function buildLayout(theme, mode, width) {{
      const isNorm = mode === "normalized";
      const y = isNorm ? yNorm : yRaw;
      const stats = computeStats(y);
      const themeLayout = theme === "dark" ? darkTheme : lightTheme;
      const tokens = getPlotTokens(theme);
      const pointAnnotations = [];
      if (stats.maxIdx >= 0) {{
        pointAnnotations.push({{
          x: x[stats.maxIdx],
          y: y[stats.maxIdx],
          text: `Max ${{formatValue(y[stats.maxIdx], mode)}}`,
          showarrow: false,
          yshift: 12,
          font: {{ color: tokens.text, size: 12 }}
        }});
      }}
      if (stats.minIdx >= 0) {{
        pointAnnotations.push({{
          x: x[stats.minIdx],
          y: y[stats.minIdx],
          text: `Min ${{formatValue(y[stats.minIdx], mode)}}`,
          showarrow: false,
          yshift: -14,
          font: {{ color: tokens.text, size: 12 }}
        }});
      }}
      if (stats.lastIdx >= 0) {{
        pointAnnotations.push({{
          x: x[stats.lastIdx],
          y: y[stats.lastIdx],
          text: "Latest",
          showarrow: false,
          xanchor: "right",
          xshift: -8,
          yshift: 10,
          font: {{ color: tokens.text, size: 12 }}
        }});
      }}
      return {{
        ...baseLayout,
        ...themeLayout,
        yaxis: buildYAxis(
          theme,
          isNorm ? "Comments per 10k HN comments" : "Comments",
          isNorm ? ",.0f" : ",d"
        ),
        xaxis: buildXAxis(theme, width),
        shapes: [
          ...buildYearLines(theme),
          {{
            type: "line",
            xref: "paper",
            x0: 0, x1: 1,
            y0: stats.avg, y1: stats.avg,
            line: {{ color: tokens.axis, width: 1, dash: "dot" }}
          }}
        ],
        annotations: [
          {{
            xref: "paper",
            x: 1,
            xanchor: "left",
            xshift: 6,
            y: stats.avg,
            text: `Avg ${{formatValue(stats.avg, mode)}}`,
            showarrow: false,
            font: {{ color: tokens.muted, size: 12 }},
            xanchor: "right",
            xshift: -6
          }},
          ...pointAnnotations
        ]
      }};
    }}

    function getPanelSize(id) {{
      const panel = document.getElementById(id);
      const fallbackH = Math.round(window.innerHeight * 0.7);
      const fallbackW = Math.round(window.innerWidth * 0.9);
      const height = panel && panel.clientHeight ? panel.clientHeight : fallbackH;
      const width = panel && panel.clientWidth ? panel.clientWidth : fallbackW;
      return {{
        height: Math.max(320, height - 16),
        width: Math.max(320, width - 16)
      }};
    }}

    function renderCharts(theme) {{
      const mainSize = getPanelSize("mainPanel");
      const mainLayout = buildLayout(theme, currentMode, mainSize.width);
      mainLayout.height = mainSize.height;
      mainLayout.width = mainSize.width;
      const plotConfig = {{
        displayModeBar: "hover",
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"],
        responsive: true
      }};
      Plotly.react("chart", buildTraces(theme, currentMode), mainLayout, {{
        ...plotConfig
      }});

      const categorySize = getPanelSize("categoryPanel");
      const categoryLayout = buildCategoryLayout(theme, currentMode, categorySize.width);
      categoryLayout.height = categorySize.height;
      categoryLayout.width = categorySize.width;
      Plotly.react("categoryChart", buildCategoryTraces(currentMode), categoryLayout, {{
        ...plotConfig
      }});
    }}

    function applyTheme(theme) {{
      document.body.dataset.theme = theme;
      renderCharts(theme);
      localStorage.setItem("hnHiringTheme", theme);
    }}

    function showTab(tab) {{
      const mainPanel = document.getElementById("mainPanel");
      const categoryPanel = document.getElementById("categoryPanel");
      const buttons = document.querySelectorAll(".tab-button");
      if (tab === "categories") {{
        mainPanel.classList.add("hidden");
        categoryPanel.classList.remove("hidden");
      }} else {{
        mainPanel.classList.remove("hidden");
        categoryPanel.classList.add("hidden");
      }}
      buttons.forEach((btn) => {{
        btn.classList.toggle("active", btn.dataset.tab === tab);
      }});
      renderCharts(document.body.dataset.theme || initialTheme);
    }}

    const storedTheme = localStorage.getItem("hnHiringTheme");
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const initialTheme = storedTheme || (prefersDark ? "dark" : "light");
    const storedMode = localStorage.getItem("hnHiringMode");
    let currentMode = storedMode || "raw";
    const modeButton = document.getElementById("modeToggle");
    const themeLabel = document.getElementById("themeLabel");
    const themeIcon = document.getElementById("themeIcon");
    const updateModeButton = () => {{
      modeButton.textContent = currentMode === "raw" ? "Normalize" : "Show raw";
    }};
    updateModeButton();
    applyTheme(initialTheme);
    themeLabel.textContent = initialTheme === "dark" ? "Dark" : "Light";
    showTab("main");

    document.getElementById("themeToggle").addEventListener("click", () => {{
      const next = document.body.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(next);
      themeLabel.textContent = next === "dark" ? "Dark" : "Light";
    }});
    document.querySelectorAll(".tab-button").forEach((btn) => {{
      btn.addEventListener("click", () => showTab(btn.dataset.tab));
    }});
    modeButton.addEventListener("click", () => {{
      currentMode = currentMode === "raw" ? "normalized" : "raw";
      localStorage.setItem("hnHiringMode", currentMode);
      updateModeButton();
      applyTheme(document.body.dataset.theme || initialTheme);
    }});
    window.addEventListener("resize", () => {{
      renderCharts(document.body.dataset.theme || initialTheme);
    }});
  </script>
  </div>
</body>
</html>
"""


def write_html(rows: List[Dict[str, Any]], path: str) -> None:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    html = build_html(rows, generated_at)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)




def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a chart of HN 'Who is hiring?' monthly comment counts."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--hits-per-page", type=int, default=200)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL verification (use only if you get cert errors).",
    )
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--cache", default="hn_who_is_hiring_hits.json")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="Skip normalization by total HN comment volume.",
    )
    parser.add_argument(
        "--hn-comments-cache",
        default="hn_total_comments_by_month.json",
        help="Cache for monthly total HN comment counts.",
    )
    parser.add_argument(
        "--hn-bucket-days",
        type=int,
        default=2,
        help="Bucket size (days) for accurate monthly comment totals.",
    )
    parser.add_argument(
        "--no-categories",
        action="store_true",
        help="Skip categorizing WIH comments.",
    )
    parser.add_argument("--category-cache", default="wih_category_counts.json")
    parser.add_argument("--category-hits-per-page", type=int, default=1000)
    parser.add_argument("--out-csv", default="who_is_hiring_comments.csv")
    parser.add_argument("--out-category-csv", default="wih_category_counts.csv")
    parser.add_argument("--out-html", default="who_is_hiring_chart.html")
    parser.add_argument(
        "--exclude-latest",
        action="store_true",
        help="Exclude the newest month from the outputs.",
    )
    args = parser.parse_args()

    hits: List[Dict[str, Any]]
    ssl_context: Optional[ssl.SSLContext]
    if args.insecure:
        ssl_context = ssl._create_unverified_context()
        print("Warning: SSL verification is disabled (--insecure).")
    else:
        ssl_context = ssl.create_default_context()
    if args.cache and os.path.exists(args.cache) and not args.refresh:
        with open(args.cache, "r", encoding="utf-8") as f:
            hits = json.load(f)
        print(f"Loaded {len(hits)} hits from cache {args.cache}")
    else:
        hits = fetch_all(
            query=args.query,
            hits_per_page=args.hits_per_page,
            delay_s=args.delay,
            timeout=args.timeout,
            ssl_context=ssl_context,
            max_pages=args.max_pages,
        )
        if args.cache:
            with open(args.cache, "w", encoding="utf-8") as f:
                json.dump(hits, f)
            print(f"Fetched {len(hits)} hits and cached to {args.cache}")

    rows, duplicates = pick_monthly_posts(hits)
    if len(rows) > 1:
        rows = rows[1:]
    if len(rows) > 0 and args.exclude_latest:
        rows = rows[:-1]
    if rows and not args.no_normalize:
        months = [r["month"] for r in rows]
        hn_counts: Dict[str, int] = {}
        if args.hn_comments_cache and os.path.exists(args.hn_comments_cache) and not args.refresh:
            with open(args.hn_comments_cache, "r", encoding="utf-8") as f:
                hn_counts = json.load(f)
            print(f"Loaded HN monthly comment totals from {args.hn_comments_cache}")
        missing = [m for m in months if m not in hn_counts]
        if missing:
            for month in missing:
                total = fetch_monthly_hn_comment_total(
                    month=month,
                    delay_s=args.delay,
                    timeout=args.timeout,
                    ssl_context=ssl_context,
                    bucket_days=args.hn_bucket_days,
                    retries=args.retries,
                    retry_delay=args.retry_delay,
                )
                hn_counts[month] = total
                if args.hn_comments_cache:
                    with open(args.hn_comments_cache, "w", encoding="utf-8") as f:
                        json.dump(hn_counts, f)
            print(f"Saved HN monthly comment totals to {args.hn_comments_cache}")
        for r in rows:
            total = hn_counts.get(r["month"])
            r["hn_total_comments"] = total
            if total:
                r["comments_per_10k"] = round((r["comments"] / total) * 10000, 2)
            else:
                r["comments_per_10k"] = None
    if rows and not args.no_categories:
        category_cache: Dict[str, Any] = {}
        category_cache_changed = False
        if args.category_cache and os.path.exists(args.category_cache) and not args.refresh:
            with open(args.category_cache, "r", encoding="utf-8") as f:
                category_cache = json.load(f)
            meta = category_cache.get("_meta") if isinstance(category_cache, dict) else None
            if not meta or meta.get("version") not in SUPPORTED_CATEGORY_CACHE_VERSIONS:
                category_cache = {}
            else:
                category_cache_changed = normalize_category_cache(category_cache)
        rules = compile_category_rules()
        for r in rows:
            story_id = str(r["object_id"])
            if story_id in category_cache and not args.refresh:
                counts = normalize_category_counts(category_cache[story_id]["counts"])
                if counts != category_cache[story_id]["counts"]:
                    category_cache[story_id]["counts"] = counts
                    category_cache_changed = True
                if sum(counts.values()) == 0:
                    counts = None
            else:
                counts = None
            if counts is None:
                counts = fetch_story_category_counts(
                    story_id=story_id,
                    delay_s=args.delay,
                    timeout=args.timeout,
                    ssl_context=ssl_context,
                    hits_per_page=args.category_hits_per_page,
                    rules=rules,
                )
                counts = normalize_category_counts(counts)
                category_cache[story_id] = {
                    "counts": counts,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                category_cache_changed = True
                if args.category_cache:
                    category_cache["_meta"] = {
                        "version": CATEGORY_CACHE_VERSION,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    with open(args.category_cache, "w", encoding="utf-8") as f:
                        json.dump(category_cache, f)
            r["categories"] = counts
            if r.get("hn_total_comments"):
                per_10k = {}
                for category in CATEGORY_ORDER:
                    per_10k[category] = round(
                        (counts.get(category, 0) / r["hn_total_comments"]) * 10000, 2
                    )
                r["categories_per_10k"] = per_10k
            else:
                r["categories_per_10k"] = None
        if args.category_cache and category_cache_changed:
            category_cache["_meta"] = {
                "version": CATEGORY_CACHE_VERSION,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(args.category_cache, "w", encoding="utf-8") as f:
                json.dump(category_cache, f)
    write_csv(rows, args.out_csv)
    if rows and not args.no_categories:
        write_category_csv(rows, args.out_category_csv, normalized=False)
        write_category_csv(rows, args.out_category_csv.replace(".csv", "_per_10k.csv"), normalized=True)
    write_html(rows, args.out_html)
    print(f"Wrote {len(rows)} months to {args.out_csv} and {args.out_html}")
    if duplicates:
        print(f"Skipped {len(duplicates)} duplicate month posts (kept highest comment count).")


if __name__ == "__main__":
    main()
