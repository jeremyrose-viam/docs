#!/usr/bin/env python3
"""
Audit external links in tutorial content, including ones htmltest can't see.

htmltest's run-htmltest-external CI job is the primary defense against dead
external links, but `.htmltest.yml`'s IgnoreURLs list blanket-exempts entire
domains (ebay.com, amazon-adjacent bot-blockers, etc.) to avoid false-positive
CI failures from rate-limiting/bot-detection. That means a genuinely dead link
on an ignored domain gets zero signal from CI, forever. This script checks
every external link under a path -- including ones on ignored domains -- and
flags which dead links are actually invisible to CI, so they can be triaged
deliberately instead of discovered by a human noticing by chance.

Usage:
    python3 scripts/audit-tutorial-links.py                    # scan docs/tutorials
    python3 scripts/audit-tutorial-links.py docs/some/path     # scan a specific path
    python3 scripts/audit-tutorial-links.py --out report.csv   # also write a CSV

Only reports; does not edit files. Network calls are real HTTP requests to
third-party sites, so avoid running this in a tight loop.
"""

import argparse
import csv
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCAN_PATH = REPO_ROOT / "docs" / "tutorials"
HTMLTEST_CONFIG = REPO_ROOT / ".htmltest.yml"

# Matches markdown links [text](url) and bare http(s) URLs, stopping at
# whitespace or markdown/HTML delimiters that are never valid in a URL.
LINK_RE = re.compile(r"https?://[^\s()<>\[\]\"'`]+")

TRAILING_PUNCTUATION = ".,;:!?`"

LOCAL_HOST_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?(/|$)")

USER_AGENT = (
    "Mozilla/5.0 (compatible; ViamDocsLinkAudit/1.0; "
    "+https://github.com/viamrobotics/docs)"
)
TIMEOUT_SECONDS = 15
MAX_WORKERS = 8

# Status codes that mean the resource is actually gone, as opposed to a
# transient/bot-blocking response that doesn't tell us much either way.
DEAD_STATUS_CODES = {404, 410}


def strip_trailing_punctuation(url):
    while url and url[-1] in TRAILING_PUNCTUATION:
        url = url[:-1]
    # Drop an unmatched trailing ")" from prose like "(see https://x.com)".
    if url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    return url


def find_links(scan_path):
    """Return a list of (file, line_number, url) for every external link found."""
    links = []
    md_files = sorted(scan_path.rglob("*.md")) if scan_path.is_dir() else [scan_path]
    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in LINK_RE.finditer(line):
                url = strip_trailing_punctuation(match.group(0))
                if url and not LOCAL_HOST_RE.match(url):
                    links.append((md_file.relative_to(REPO_ROOT), lineno, url))
    return links


def load_ignored_patterns():
    """Pull the IgnoreURLs regex list out of .htmltest.yml without needing pyyaml."""
    patterns = []
    in_section = False
    for line in HTMLTEST_CONFIG.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "IgnoreURLs:":
            in_section = True
            continue
        if in_section:
            if stripped.startswith("#"):
                continue
            match = re.match(r'^-\s*"(.+)"\s*$', stripped)
            if match:
                patterns.append(match.group(1))
                continue
            # Any non-comment, non-list-item line ends the section.
            if stripped and not stripped.startswith("-"):
                break
    return [re.compile(p) for p in patterns]


def is_ignored_by_htmltest(url, ignored_patterns):
    return any(p.search(url) for p in ignored_patterns)


def check_url(url):
    """Return (status_description, http_status_or_none).

    Uses GET, not HEAD: several real-world redirectors (Amazon's a.co
    shortener, marketplace.visualstudio.com) return 404 for HEAD on links
    that resolve fine on GET, which produced false "dead link" positives.
    The response body is never read, so this doesn't cost much extra.
    """
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return "OK", response.status
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}", e.code
    except Exception as e:
        return f"ERROR ({e})", None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_SCAN_PATH),
        help="File or directory to scan for links (default: docs/tutorials)",
    )
    parser.add_argument("--out", help="Optional path to write a full CSV report to")
    args = parser.parse_args()

    scan_path = Path(args.path).resolve()
    if not scan_path.exists():
        print(f"error: {scan_path} does not exist", file=sys.stderr)
        return 1

    links = find_links(scan_path)
    if not links:
        print(f"No external links found under {scan_path}.")
        return 0

    ignored_patterns = load_ignored_patterns()

    unique_urls = sorted({url for _, _, url in links})
    print(f"Found {len(links)} link references ({len(unique_urls)} unique URLs) under {scan_path}.")
    print(f"Checking {len(unique_urls)} unique URLs (concurrency={MAX_WORKERS}, timeout={TIMEOUT_SECONDS}s)...\n")

    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_url = {pool.submit(check_url, url): url for url in unique_urls}
        done = 0
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            results[url] = future.result()
            done += 1
            if done % 25 == 0 or done == len(unique_urls):
                print(f"  ...checked {done}/{len(unique_urls)}")

    rows = []
    for file_path, lineno, url in links:
        status_desc, status_code = results[url]
        ignored = is_ignored_by_htmltest(url, ignored_patterns)
        ok = status_code is not None and 200 <= status_code < 400
        is_dead = status_code in DEAD_STATUS_CODES
        if ok:
            category = "ok"
        elif ignored and is_dead:
            category = "blind_spot"  # confirmed dead, invisible to CI
        elif ignored:
            category = "needs_human"  # ambiguous on a domain CI can't check either
        else:
            category = "ci_visible"  # CI's own htmltest job already catches this
        rows.append(
            {
                "file": str(file_path),
                "line": lineno,
                "url": url,
                "status": status_desc,
                "ignored_by_htmltest": ignored,
                "category": category,
            }
        )

    blind_spots = [r for r in rows if r["category"] == "blind_spot"]
    needs_human = [r for r in rows if r["category"] == "needs_human"]
    ci_visible = [r for r in rows if r["category"] == "ci_visible"]

    print("\n" + "=" * 72)
    if blind_spots:
        print(f"\nBLIND SPOTS: confirmed dead links on domains htmltest ignores ({len(blind_spots)}):")
        for r in blind_spots:
            print(f"  {r['file']}:{r['line']}  [{r['status']}]  {r['url']}")
    else:
        print("\nNo confirmed blind-spot dead links found.")

    if needs_human:
        print(f"\nNEEDS A HUMAN LOOK: ignored-domain links returning an error, but not "
              f"conclusively dead (bot-blocking looks the same as a dead page) ({len(needs_human)}):")
        for r in needs_human:
            print(f"  {r['file']}:{r['line']}  [{r['status']}]  {r['url']}")

    if ci_visible:
        print(f"\nAlready CI-visible (htmltest should already be flagging these) ({len(ci_visible)}):")
        for r in ci_visible:
            print(f"  {r['file']}:{r['line']}  [{r['status']}]  {r['url']}")

    print(f"\n{len(unique_urls)} unique URLs checked, {len(blind_spots)} confirmed blind-spot dead links, "
          f"{len(needs_human)} ambiguous ignored-domain links needing a human look, "
          f"{len(ci_visible)} CI-visible failures.")

    if args.out:
        out_path = Path(args.out)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["file", "line", "url", "status", "ignored_by_htmltest", "category"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nFull report written to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
