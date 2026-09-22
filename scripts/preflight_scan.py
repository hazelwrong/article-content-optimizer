#!/usr/bin/env python3
"""Read-only HTML candidate scan, used after editorial optimization.

Never infers intent from product/link counts, approves publication, or proves
live indexing. Structured warnings distinguish content from implementation;
missing page metadata in a fragment is unknown, not an observed defect.
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
    "PROMPT_OR_DEBUG_TEXT": re.compile(
        r"\b(?:lorem ipsum|as an ai language model|system prompt|debug output|todo placeholder)\b"
        r"|(?:提示词|调试输出|待替换变量)", re.I,
    ),
    "PRODUCTION_PROCESS_TEXT": re.compile(
        r"\b(?:future product module|product module (?:will|may)|"
        r"(?:affiliate|marketplace|product) links? (?:will be|to be) (?:added|inserted)|"
        r"PRODUCT_MODULE_SLOT|insert (?:product|affiliate) links? here)\b"
        r"|(?:待补(?:商品|链接)|后续(?:添加|插入|补充)(?:商品模块|商品链接)|生产占位)", re.I,
    ),
    "SELF_UNDERMINING_TEST_STATEMENT": re.compile(
        r"\b(?:we|this (?:article|guide|review))\s+"
        r"(?:have\s+not|haven['’]t|did\s+not|didn['’]t|do\s+not|don['’]t|does\s+not)\s+"
        r"(?:personally\s+|independently\s+)?(?:test(?:ed)?|conduct(?:ed)?\s+(?:any\s+)?(?:tests?|experiments?))\b"
        r"|(?:本文|我们).{0,8}(?:未|没有).{0,12}(?:实测|测试|实验)", re.I,
    ),
}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
IGNORED_TAGS = {"script", "style", "head", "template", "noscript"}
ARTICLE_CLASSES = {"dhc-page", "sports-article", "article-content"}


class ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.texts: dict[str, list[str]] = {key: [] for key in ("article", "main", "body", "fragment")}
        self.h1: list[str] = []
        self._heading_text: list[str] | None = None
        self.canonicals: list[str] = []
        self.robots: list[str] = []
        self.http_links: list[str] = []
        self.image_count = 0
        self.production_comments: list[str] = []
        self.seen_html = False
        self.seen_head = False

    def ignored(self) -> bool:
        return any(tag in IGNORED_TAGS for tag, _ in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key: value or "" for key, value in attrs}
        self.seen_html |= tag == "html"
        self.seen_head |= tag == "head"
        # Read metadata in head, but not markup embedded in inert templates.
        inert = any(name in {"template", "script", "style", "noscript"} for name, _ in self.stack)
        if not inert:
            if tag == "link" and "canonical" in data.get("rel", "").lower().split():
                self.canonicals.append(data.get("href", ""))
            if tag == "meta" and data.get("name", "").lower() in {"robots", "googlebot"}:
                self.robots.append(data.get("content", ""))
        visible = not self.ignored() and tag not in IGNORED_TAGS
        if visible:
            if tag in {"br", "hr"}:
                self.handle_data(" ")
            if tag == "h1":
                self._heading_text = []
            if tag == "a" and re.match(r"https?://", data.get("href", ""), re.I):
                self.http_links.append(data["href"])
            if tag == "img":
                self.image_count += 1
        if tag not in VOID_TAGS:
            is_article = tag == "article" or bool(ARTICLE_CLASSES.intersection(data.get("class", "").split()))
            self.stack.append((tag, is_article))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._heading_text is not None:
            self.h1.append(" ".join("".join(self._heading_text).split()))
            self._heading_text = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in {"p", "div", "section", "article", "main", "li", "h1", "h2", "h3", "td", "tr"}:
            self.handle_data(" ")

    def handle_data(self, data: str) -> None:
        if self.ignored():
            return
        if self._heading_text is not None:
            self._heading_text.append(data)
        self.texts["fragment"].append(data)
        tags = {tag for tag, _ in self.stack}
        if "body" in tags:
            self.texts["body"].append(data)
        if "main" in tags:
            self.texts["main"].append(data)
        if any(is_article for _, is_article in self.stack):
            self.texts["article"].append(data)

    def handle_comment(self, data: str) -> None:
        if re.search(r"\bPRODUCT_MODULE_SLOT\b", data, re.I):
            self.production_comments.append(data.strip())

    def content(self) -> tuple[str, str]:
        for scope, chunks in self.texts.items():
            value = " ".join("".join(chunks).split())
            if value:
                return scope, value
        return "unavailable", ""


def parse_html(path: Path) -> tuple[ArticleParser, str]:
    raw = path.read_text(encoding="utf-8")
    parser = ArticleParser()
    parser.feed(raw)
    parser.close()
    return parser, raw


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())


def cosine_similarity(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = Counter(left), Counter(right)
    dot = sum(value * b[key] for key, value in a.items())
    denominator = math.sqrt(sum(value * value for value in a.values())) * math.sqrt(sum(value * value for value in b.values()))
    return dot / denominator if denominator else 0.0


def scan(path: Path) -> dict:
    parser, _ = parse_html(path)
    scope, article_text = parser.content()
    full_document = parser.seen_html and parser.seen_head
    warnings: list[dict[str, str]] = []

    def warn(code: str, category: str, evidence: str) -> None:
        warnings.append({"code": code, "category": category, "evidence": evidence, "status": "candidate_review_required"})

    if full_document and len(parser.h1) != 1:
        warn("H1_COUNT", "technical", str(len(parser.h1)))
    if full_document and len(parser.canonicals) != 1:
        warn("CANONICAL_COUNT", "technical", str(len(parser.canonicals)))
    if any(not url.strip() for url in parser.canonicals):
        warn("EMPTY_CANONICAL", "technical", "Observed an empty canonical href")
    if any(re.search(r"\b(?:noindex|none)\b", value, re.I) for value in parser.robots):
        warn("NOINDEX", "technical", ", ".join(parser.robots))
    for code, pattern in SUSPICIOUS_PATTERNS.items():
        for match in pattern.finditer(article_text):
            warn(code, "content", match.group(0)[:240])
    for comment in parser.production_comments:
        warn("PRODUCTION_PLACEHOLDER_COMMENT", "content", "HTML comment (not visible reader text): " + comment[:180])

    return {
        "file": str(path),
        "title": parser.h1[0] if parser.h1 else "",
        "text_scope": scope,
        "input_scope": "local_full_html" if full_document else "local_fragment_or_partial_html",
        "token_count": len(tokens(article_text)),
        "h1_count": len(parser.h1),
        "canonicals": parser.canonicals,
        "robots": parser.robots,
        "distinct_http_links": len(set(parser.http_links)),
        "image_count": parser.image_count,
        "warnings": warnings,
        "limitations": [
            "Read-only static candidates; no intent, product sufficiency, ranking or publication decision.",
            "Live HTTP/headers/robots.txt, rendering, performance, Schema semantics and index status are not checked.",
            "Text uses article/main/body/fallback priority; navigation may remain in fallback scope. Manually verify extraction.",
            "No static matches does not prove absence of semantic leakage or loss of useful details.",
        ] + ([] if full_document else ["Missing H1/canonical is not diagnosed because this input may omit page-level markup."]),
        "_tokens": tokens(article_text),
    }


def main() -> None:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("files", nargs="+", type=Path)
    argument_parser.add_argument("--compare-dir", type=Path, help="Optional lexical comparison only; never proves duplicate intent.")
    argument_parser.add_argument("--similarity-threshold", type=float, default=0.72)
    args = argument_parser.parse_args()
    results = [scan(path) for path in args.files]
    comparison: list[tuple[Path, dict]] = []
    if args.compare_dir:
        input_paths = {path.resolve() for path in args.files}
        for candidate in sorted(args.compare_dir.glob("*.html")):
            if candidate.resolve() not in input_paths:
                comparison.append((candidate, scan(candidate)))
    for result in results:
        similarities = []
        for candidate, other in comparison:
            score = cosine_similarity(result["_tokens"], other["_tokens"])
            if score >= args.similarity_threshold:
                similarities.append({"file": str(candidate), "cosine_similarity": round(score, 3), "meaning": "lexical_overlap_only"})
        result["similar_pages"] = sorted(similarities, key=lambda item: item["cosine_similarity"], reverse=True)
        result.pop("_tokens", None)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
