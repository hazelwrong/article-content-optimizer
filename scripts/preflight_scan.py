#!/usr/bin/env python3
"""Static preflight checks for article HTML.

Reports candidates for editorial review. It never makes a publish decision and
uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


SUSPICIOUS_PATTERNS = {
    "MONITOR_GEOMETRY_ARTIFACT": re.compile(
        r"turn diagonal.{0,180}(?:geometry|eye distance)", re.I | re.S
    ),
    "BATTERY_CALCULATOR_ARTIFACT": re.compile(
        r"model battery energy.{0,180}(?:efficiency|taper|charger)", re.I | re.S
    ),
    "PROMPT_OR_DEBUG_TEXT": re.compile(
        r"\b(?:lorem ipsum|as an ai language model|system prompt|debug output|todo placeholder)\b",
        re.I,
    ),
}

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.article_depth = 0
        self.in_article = False
        self.text: list[str] = []
        self.h1: list[str] = []
        self._heading: str | None = None
        self._heading_text: list[str] = []
        self.canonicals: list[str] = []
        self.robots: list[str] = []
        self.product_links: list[str] = []
        self.external_links: list[str] = []
        self.article_images = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key: value or "" for key, value in attrs}
        classes = data.get("class", "").split()
        if tag == "article" and "dhc-page" in classes:
            self.in_article = True
            self.article_depth = 1
        elif self.in_article and tag not in VOID_TAGS:
            self.article_depth += 1

        if tag == "link" and "canonical" in data.get("rel", "").lower():
            self.canonicals.append(data.get("href", ""))
        if tag == "meta" and data.get("name", "").lower() == "robots":
            self.robots.append(data.get("content", ""))
        if tag == "h1":
            self._heading = "h1"
            self._heading_text = []
        if self.in_article and tag == "img":
            self.article_images += 1
        if self.in_article and tag == "a":
            href = data.get("href", "")
            if href.startswith("http"):
                self.external_links.append(href)
            if "dhgate.com/product/" in href:
                self.product_links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._heading == "h1":
            value = " ".join(self._heading_text).strip()
            if value:
                self.h1.append(value)
            self._heading = None
            self._heading_text = []
        if self.in_article and tag not in VOID_TAGS:
            self.article_depth -= 1
            if self.article_depth <= 0:
                self.in_article = False
                self.article_depth = 0

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if not clean:
            return
        if self._heading == "h1":
            self._heading_text.append(clean)
        if self.in_article:
            self.text.append(clean)


def parse_html(path: Path) -> tuple[ArticleParser, str]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    parser = ArticleParser()
    parser.feed(raw)
    return parser, raw


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def cosine_similarity(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = Counter(left), Counter(right)
    dot = sum(value * b[key] for key, value in a.items())
    denominator = math.sqrt(sum(value * value for value in a.values())) * math.sqrt(
        sum(value * value for value in b.values())
    )
    return dot / denominator if denominator else 0.0


def scan(path: Path) -> dict:
    parser, _raw = parse_html(path)
    article_text = " ".join(parser.text)
    title = parser.h1[0] if parser.h1 else ""
    warnings: list[dict[str, str]] = []

    if len(parser.h1) != 1:
        warnings.append({"code": "H1_COUNT", "evidence": str(len(parser.h1))})
    if len(parser.canonicals) != 1:
        warnings.append({"code": "CANONICAL_COUNT", "evidence": str(len(parser.canonicals))})
    if any("noindex" in value.lower() for value in parser.robots):
        warnings.append({"code": "NOINDEX", "evidence": ", ".join(parser.robots)})

    for code, pattern in SUSPICIOUS_PATTERNS.items():
        match = pattern.search(article_text)
        if match:
            warnings.append({"code": code, "evidence": match.group(0)[:240]})

    lower_title = title.lower()
    product_count = len(set(parser.product_links))
    if re.search(r"\b(best|top)\b|\b20\d{2}\b", lower_title) and product_count < 3:
        warnings.append({
            "code": "BEST_PROMISE_LOW_PRODUCT_COUNT",
            "evidence": f"{product_count} distinct product links",
        })
    if "collection" in lower_title and product_count < 3:
        warnings.append({
            "code": "COLLECTION_PROMISE_LOW_PRODUCT_COUNT",
            "evidence": f"{product_count} distinct product links",
        })
    if re.search(r"\b(perfume|fragrance)\b", lower_title):
        for match in re.finditer(r"\b(\d+(?:\.\d+)?)\s*oz\b", article_text, re.I):
            if float(match.group(1)) > 16:
                warnings.append({
                    "code": "SUSPICIOUS_FRAGRANCE_VOLUME",
                    "evidence": match.group(0),
                })

    return {
        "file": str(path),
        "title": title,
        "word_count": len(tokens(article_text)),
        "h1_count": len(parser.h1),
        "canonical_count": len(parser.canonicals),
        "robots": parser.robots,
        "distinct_product_links": product_count,
        "external_link_count": len(set(parser.external_links)),
        "article_image_count": parser.article_images,
        "warnings": warnings,
        "_tokens": tokens(article_text),
    }


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("files", nargs="+", type=Path)
    argument_parser.add_argument("--compare-dir", type=Path)
    argument_parser.add_argument("--similarity-threshold", type=float, default=0.72)
    args = argument_parser.parse_args()

    results = [scan(path) for path in args.files]
    comparison: list[tuple[Path, dict]] = []
    if args.compare_dir:
        input_paths = {path.resolve() for path in args.files}
        for candidate in args.compare_dir.glob("*.html"):
            if candidate.resolve() not in input_paths:
                comparison.append((candidate, scan(candidate)))

    for result in results:
        similarities = []
        for candidate, other in comparison:
            score = cosine_similarity(result["_tokens"], other["_tokens"])
            if score >= args.similarity_threshold:
                similarities.append({
                    "file": str(candidate),
                    "cosine_similarity": round(score, 3),
                })
        result["similar_pages"] = sorted(
            similarities,
            key=lambda item: item["cosine_similarity"],
            reverse=True,
        )
        result.pop("_tokens", None)

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
