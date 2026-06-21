#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape as html_escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_ENDPOINT = "https://hn.algolia.com/api/v1/search_by_date"
DEFAULT_QUERY = '"Ask HN: Who is hiring?"'
DEFAULT_AUTHOR = "whoishiring"
DEFAULT_CACHE_PATH = Path("data/who_is_hiring_algolia_cache.json")
DEFAULT_CSV_PATH = Path("out/who_is_hiring_comments.csv")
DEFAULT_SVG_PATH = Path("out/who_is_hiring_comments.svg")

USER_AGENT = "HNHiringChart/1.0 (+https://news.ycombinator.com/)"

MONTH_YEAR_RE = re.compile(r"\(([^()]+)\)\s*$")
MONTH_TO_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass(frozen=True)
class HiringPost:
    month: date
    hn_id: str
    title: str
    comments: int
    created_at_i: int
    author: str
    points: int


def _mkdirp(path: Path) -> None:
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)


def _http_get_json(url: str, params: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    full_url = f"{url}?{urlencode(params)}"
    req = Request(full_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read().decode(charset)
    except HTTPError as e:
        raise RuntimeError(f"HTTP error {e.code} for {full_url}") from e
    except URLError as e:
        raise RuntimeError(f"Network error for {full_url}: {e.reason}") from e
    return json.loads(body)


def fetch_algolia_hits(
    *,
    endpoint: str,
    query: str,
    tags: str,
    hits_per_page: int,
    max_pages: int | None,
    timeout_s: float,
    sleep_s: float,
    verbose: bool,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    page = 0
    nb_pages: int | None = None
    while True:
        if max_pages is not None and page >= max_pages:
            break
        params: dict[str, Any] = {
            "query": query,
            "tags": tags,
            "hitsPerPage": hits_per_page,
            "page": page,
        }
        payload = _http_get_json(endpoint, params, timeout_s=timeout_s)
        page_hits = payload.get("hits", [])
        if not isinstance(page_hits, list):
            raise RuntimeError("Unexpected Algolia response: 'hits' is not a list")
        hits.extend(page_hits)
        if nb_pages is None:
            nb_pages = int(payload.get("nbPages", 0))
        if verbose:
            print(f"Fetched page {page + 1}/{nb_pages or '?'} (+{len(page_hits)} hits)")
        page += 1
        if nb_pages is not None and page >= nb_pages:
            break
        if sleep_s > 0:
            time.sleep(sleep_s)
    return hits


def load_or_fetch_hits(
    *,
    cache_path: Path | None,
    refresh: bool,
    endpoint: str,
    query: str,
    author: str | None,
    hits_per_page: int,
    max_pages: int | None,
    timeout_s: float,
    sleep_s: float,
    verbose: bool,
) -> list[dict[str, Any]]:
    if cache_path is not None and cache_path.exists() and not refresh:
        with cache_path.open("r", encoding="utf-8") as f:
            cached = json.load(f)
        if isinstance(cached, dict) and isinstance(cached.get("hits"), list):
            return cached["hits"]
        if isinstance(cached, list):
            return cached
        raise RuntimeError(f"Unrecognized cache format in {cache_path}")

    tags = "story"
    if author:
        tags = f"{tags},author_{author}"

    hits = fetch_algolia_hits(
        endpoint=endpoint,
        query=query,
        tags=tags,
        hits_per_page=hits_per_page,
        max_pages=max_pages,
        timeout_s=timeout_s,
        sleep_s=sleep_s,
        verbose=verbose,
    )

    if cache_path is not None:
        _mkdirp(cache_path)
        payload = {
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "endpoint": endpoint,
            "query": query,
            "author": author,
            "hits": hits,
        }
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")

    return hits


def _parse_month_from_title(title: str) -> date | None:
    m = MONTH_YEAR_RE.search(title.strip())
    if not m:
        return None
    inner = m.group(1).strip().replace(",", "")
    parts = inner.split()
    if len(parts) != 2:
        return None
    month_s, year_s = parts[0].strip().lower(), parts[1].strip()
    if not year_s.isdigit():
        return None
    month_num = MONTH_TO_NUM.get(month_s)
    if month_num is None:
        return None
    return date(int(year_s), month_num, 1)


def _parse_month_from_hit(hit: dict[str, Any]) -> date:
    title = str(hit.get("title") or "")
    by_title = _parse_month_from_title(title)
    if by_title is not None:
        return by_title

    created_at_i = hit.get("created_at_i")
    if isinstance(created_at_i, int):
        dt = datetime.fromtimestamp(created_at_i, tz=timezone.utc).date()
        return date(dt.year, dt.month, 1)

    created_at = hit.get("created_at")
    if isinstance(created_at, str):
        # Algolia uses ISO 8601 like "2024-01-01T00:00:00.000Z"
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
            return date(dt.year, dt.month, 1)
        except ValueError:
            pass

    raise RuntimeError("Hit is missing both 'created_at_i' and a parseable 'created_at'")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_hiring_posts(hits: list[dict[str, Any]]) -> list[HiringPost]:
    by_month: dict[date, HiringPost] = {}

    for hit in hits:
        title = str(hit.get("title") or "")
        if "who is hiring" not in title.lower():
            continue

        month = _parse_month_from_hit(hit)
        hn_id = str(hit.get("objectID") or "")
        comments = _to_int(hit.get("num_comments"), default=0)
        created_at_i = _to_int(hit.get("created_at_i"), default=0)
        author = str(hit.get("author") or "")
        points = _to_int(hit.get("points"), default=0)

        post = HiringPost(
            month=month,
            hn_id=hn_id,
            title=title,
            comments=comments,
            created_at_i=created_at_i,
            author=author,
            points=points,
        )

        existing = by_month.get(month)
        if existing is None:
            by_month[month] = post
            continue

        if (post.comments, post.points, post.created_at_i) > (
            existing.comments,
            existing.points,
            existing.created_at_i,
        ):
            by_month[month] = post

    return [by_month[m] for m in sorted(by_month)]


def write_csv(posts: list[HiringPost], csv_path: Path) -> None:
    _mkdirp(csv_path)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["month", "hn_id", "comments", "title", "author", "points", "hn_url"])
        for p in posts:
            month_s = f"{p.month.year:04d}-{p.month.month:02d}"
            hn_url = f"https://news.ycombinator.com/item?id={p.hn_id}"
            w.writerow([month_s, p.hn_id, p.comments, p.title, p.author, p.points, hn_url])


def _nice_tick_step(max_value: int, target_ticks: int = 6) -> int:
    if max_value <= 0:
        return 1
    raw = max_value / max(1, target_ticks - 1)
    base = 10 ** int(math.floor(math.log10(raw)))
    for mult in (1, 2, 5, 10):
        step = int(base * mult)
        if step >= raw:
            return step
    return int(base * 10)


def _round_up(value: int, step: int) -> int:
    return int(math.ceil(value / step) * step)


def write_svg(posts: list[HiringPost], svg_path: Path, *, width: int, height: int, title: str) -> None:
    if not posts:
        raise RuntimeError("No posts found. Try removing the author filter or using --refresh.")

    _mkdirp(svg_path)

    margin_l, margin_r, margin_t, margin_b = 70, 20, 50, 70
    plot_w = max(1, width - margin_l - margin_r)
    plot_h = max(1, height - margin_t - margin_b)

    comments = [p.comments for p in posts]
    max_y = max(comments)
    step = _nice_tick_step(max_y, target_ticks=7)
    y_max = max(step, _round_up(max_y, step))

    def x_at(i: int) -> float:
        if len(posts) == 1:
            return float(margin_l + plot_w / 2)
        return float(margin_l + (plot_w * i) / (len(posts) - 1))

    def y_at(v: int) -> float:
        return float(margin_t + plot_h * (1 - (v / y_max)))

    points = [(x_at(i), y_at(p.comments), p) for i, p in enumerate(posts)]

    # X labels: show years at January, but keep it readable.
    year_indices = [i for i, p in enumerate(posts) if p.month.month == 1]
    max_year_labels = 12
    year_step = 1
    if len(year_indices) > max_year_labels:
        year_step = int(math.ceil(len(year_indices) / max_year_labels))
    labeled_year_indices = set(year_indices[::year_step])
    labeled_year_indices.add(0)
    labeled_year_indices.add(len(posts) - 1)

    path_parts: list[str] = []
    for i, (x, y, _) in enumerate(points):
        cmd = "M" if i == 0 else "L"
        path_parts.append(f"{cmd}{x:.2f},{y:.2f}")
    path_d = " ".join(path_parts)

    y_ticks = list(range(0, y_max + 1, step))

    svg_lines: list[str] = []
    svg_lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">')
    svg_lines.append('<style><![CDATA[')
    svg_lines.append(
        "text{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:12px;fill:#111}"
    )
    svg_lines.append(".muted{fill:#666}")
    svg_lines.append(".grid{stroke:#eee;stroke-width:1}")
    svg_lines.append(".axis{stroke:#111;stroke-width:1.2}")
    svg_lines.append(".line{stroke:#2563eb;stroke-width:2.0;fill:none}")
    svg_lines.append(".dot{fill:#2563eb;stroke:#fff;stroke-width:1.0}")
    svg_lines.append("]]></style>")

    svg_lines.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>')
    svg_lines.append(
        f'<text x="{margin_l}" y="{margin_t - 20}" font-size="18" font-weight="600">{html_escape(title)}</text>'
    )

    # Grid + Y labels
    for t in y_ticks:
        y = y_at(t)
        svg_lines.append(f'<line class="grid" x1="{margin_l}" y1="{y:.2f}" x2="{margin_l + plot_w}" y2="{y:.2f}"/>')
        svg_lines.append(
            f'<text class="muted" x="{margin_l - 10}" y="{y + 4:.2f}" text-anchor="end">{t}</text>'
        )

    # Axes
    svg_lines.append(f'<line class="axis" x1="{margin_l}" y1="{margin_t}" x2="{margin_l}" y2="{margin_t + plot_h}"/>')
    svg_lines.append(
        f'<line class="axis" x1="{margin_l}" y1="{margin_t + plot_h}" x2="{margin_l + plot_w}" y2="{margin_t + plot_h}"/>'
    )

    # Line
    svg_lines.append(f'<path class="line" d="{path_d}"/>')

    # Dots + hover tooltips + links
    for x, y, p in points:
        month_s = f"{p.month.year:04d}-{p.month.month:02d}"
        hn_url = f"https://news.ycombinator.com/item?id={p.hn_id}"
        tooltip = f"{month_s}: {p.comments} comments"
        svg_lines.append(f'<a href="{hn_url}" target="_blank" rel="noopener noreferrer">')
        svg_lines.append(f'<circle class="dot" cx="{x:.2f}" cy="{y:.2f}" r="3.5"><title>{html_escape(tooltip)}</title></circle>')
        svg_lines.append("</a>")

    # X labels (years)
    y_label = margin_t + plot_h + 30
    for i in sorted(labeled_year_indices):
        x = x_at(i)
        p = posts[i]
        label = str(p.month.year) if p.month.month == 1 else f"{p.month.year:04d}-{p.month.month:02d}"
        svg_lines.append(f'<line class="grid" x1="{x:.2f}" y1="{margin_t}" x2="{x:.2f}" y2="{margin_t + plot_h}"/>')
        svg_lines.append(f'<text class="muted" x="{x:.2f}" y="{y_label}" text-anchor="middle">{html_escape(label)}</text>')

    # Axis labels
    svg_lines.append(
        f'<text class="muted" x="{margin_l}" y="{height - 15}" text-anchor="start">Hover a point for details. Click to open the HN post.</text>'
    )
    svg_lines.append("</svg>")

    with svg_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(svg_lines))
        f.write("\n")


def main() -> int:
    p = argparse.ArgumentParser(description="Chart HN Who Is Hiring monthly post comment counts.")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--query", default=DEFAULT_QUERY)
    p.add_argument("--author", default=DEFAULT_AUTHOR, help="HN username to filter by (set empty to disable).")
    p.add_argument("--cache", default=str(DEFAULT_CACHE_PATH))
    p.add_argument("--refresh", action="store_true", help="Fetch fresh data (ignore cache).")
    p.add_argument("--hits-per-page", type=int, default=1000)
    p.add_argument("--max-pages", type=int, default=None)
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--sleep", type=float, default=0.2, help="Seconds to sleep between page requests.")
    p.add_argument("--csv", default=str(DEFAULT_CSV_PATH))
    p.add_argument("--svg", default=str(DEFAULT_SVG_PATH))
    p.add_argument("--width", type=int, default=1200)
    p.add_argument("--height", type=int, default=600)
    p.add_argument("--title", default="HN Ask: Who is hiring? — comments per month")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    cache_path = Path(args.cache) if args.cache else None
    author = args.author.strip() if isinstance(args.author, str) else ""
    author = author if author else None

    hits = load_or_fetch_hits(
        cache_path=cache_path,
        refresh=args.refresh,
        endpoint=args.endpoint,
        query=args.query,
        author=author,
        hits_per_page=args.hits_per_page,
        max_pages=args.max_pages,
        timeout_s=args.timeout,
        sleep_s=args.sleep,
        verbose=args.verbose,
    )

    posts = parse_hiring_posts(hits)
    write_csv(posts, Path(args.csv))
    write_svg(posts, Path(args.svg), width=args.width, height=args.height, title=args.title)

    if args.verbose:
        print(f"Wrote {args.csv} and {args.svg} ({len(posts)} months)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
