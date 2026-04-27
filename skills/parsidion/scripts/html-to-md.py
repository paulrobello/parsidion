#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "beautifulsoup4",
#   "html2text",
#   "httpx",
# ]
# ///
"""Convert HTML to clean markdown optimised for LLM consumption.

Reads HTML from a file, stdin, or URL, strips navigation, banners, cookie
notices, layout tables, and script/style elements, then emits clean markdown
with preserved code-fence language annotations.

Usage:
  html-to-md [FILE]                  # convert file → stdout
  html-to-md [FILE] -o out.md        # convert file → file
  html-to-md -                       # read stdin → stdout
  html-to-md https://example.com     # fetch URL → stdout
  html-to-md https://example.com -o page.md

Options:
  FILE | URL | -       Input source (URL, file path, or - for stdin)
  -o, --output FILE    Write output to FILE instead of stdout
  --url URL            Base URL for resolving relative links when input is
                       a file or stdin (ignored when fetching a URL directly)
  --no-links           Strip all hyperlinks from output
  --include-images     Preserve image references in output
  --debug, -D          Print extra info to stderr
  --help, -h           Show this message and exit

Examples:
  html-to-md page.html
  html-to-md page.html -o page.md
  curl https://example.com | html-to-md - --url https://example.com
  html-to-md https://docs.python.org/3/library/re.html -o re-docs.md
"""

from __future__ import annotations

import argparse
import re
import sys
from urllib.parse import urljoin, urlparse

import html2text  # type: ignore[import-untyped]
import httpx  # type: ignore[import-untyped]
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

# ── Constants (mirrored from par-fetch-mcp) ────────────────────────────────

_LANG_MARKER_PREFIX = "<!--lang:"
_LANG_MARKER_SUFFIX = "-->"

_COOKIE_CONSENT_PATTERN = re.compile(
    r"cookie|consent|gdpr|privacy.?banner|cc-banner|onetrust|CybotCookiebot",
    re.IGNORECASE,
)

_SELECTORS_TO_REMOVE = [
    '[role="navigation"]',
    '[role="banner"]',
    '[role="contentinfo"]',
    '[role="complementary"]',
    '[role="search"]',
    '[aria-hidden="true"]',
    ".skip-to-content",
    ".skip-link",
    "[class*='skip-to']",
    "[class*='social-share']",
    "[class*='share-button']",
    "[class*='social-media']",
]


# ── Core pipeline (mirrored from par-fetch-mcp) ────────────────────────────


def _extract_code_language(tag) -> str:
    """Extract the programming language identifier from a code or pre element.

    Checks the element itself and all descendant ``code`` elements for CSS class
    names that begin with ``language-``, ``lang-``, or ``highlight-``. Returns
    the first match found.

    Args:
        tag: A BeautifulSoup tag object representing a ``pre`` or ``code`` element.

    Returns:
        The language identifier (e.g. ``"python"``, ``"bash"``), or an empty
        string if no language class is present.
    """
    for el in [tag] + tag.find_all("code"):
        classes = el.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        for cls in classes:
            for prefix in ("language-", "lang-", "highlight-"):
                if cls.startswith(prefix):
                    return cls[len(prefix) :]
    return ""


def _is_layout_table(table) -> bool:
    """Determine whether a table is used for layout rather than data presentation.

    A table is considered a layout table when it carries ``role="presentation"``
    or when it has no header cells (``th``) and every row contains at most one
    cell. Both heuristics are commonly produced by CSS-driven multi-column
    layouts that should be unwrapped rather than converted to Markdown tables.

    Args:
        table: A BeautifulSoup tag object for a ``table`` element.

    Returns:
        True if the table appears to be a layout table; False otherwise.
    """
    if table.get("role") == "presentation":
        return True
    if not table.find("th"):
        rows = table.find_all("tr")
        if rows and all(len(row.find_all(["td", "th"])) <= 1 for row in rows):
            return True
    return False


def _clean_markdown(text: str) -> str:
    """Remove common Markdown noise produced by html2text on real-world pages.

    Applies a sequence of regex substitutions to eliminate artefacts such as
    excessive blank lines, empty list bullets, empty links, stray emphasis
    markers, consecutive horizontal rules, and blank table-separator rows.

    Args:
        text: Raw Markdown string produced by ``html2text``.

    Returns:
        Cleaned Markdown string with trailing whitespace stripped.
    """
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = re.sub(r"^[*\-+] *$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[\s*\]\([^)]*\)", "", text)
    text = re.sub(r"\*{1,3}\s*\*{1,3}", "", text)
    text = re.sub(r"_{1,3}\s*_{1,3}", "", text)
    text = re.sub(r"(^-{3,}\s*$\n?){2,}", "---\n", text, flags=re.MULTILINE)
    text = re.sub(r"(^\*{3,}\s*$\n?){2,}", "---\n", text, flags=re.MULTILINE)
    text = re.sub(r"^[|\-\s]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _html_to_markdown(
    html_content: str,
    *,
    url: str | None = None,
    include_links: bool = True,
    include_images: bool = False,
) -> str:
    """Convert an HTML string to clean Markdown optimised for LLM consumption.

    Narrows the parse tree to the main content container when one is present
    (``main``, ``[role='main']``, or ``article``), strips non-content elements
    (navigation, banners, footers, scripts, cookie notices, layout tables),
    resolves relative URLs when a base URL is provided, annotates fenced code
    blocks with language identifiers, then delegates to ``html2text`` for the
    final Markdown conversion. The result is post-processed by ``_clean_markdown``
    to remove common conversion artefacts.

    Args:
        html_content: Raw HTML string to convert.
        url: Base URL used to resolve relative ``href``/``src`` attributes.
            Ignored when ``include_links`` is False.
        include_links: When True (default), hyperlinks are preserved in the
            output. When False, ``a`` and ``link`` elements are removed.
        include_images: When True, ``img`` elements are preserved as Markdown
            image references. Defaults to False.

    Returns:
        Cleaned Markdown string ready for LLM consumption.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Narrow to main content container if one exists
    content_root = None
    for selector in ["main", "[role='main']", "article"]:
        candidate = soup.select_one(selector)
        if candidate and len(candidate.get_text(strip=True)) > 200:
            content_root = candidate
            break

    if content_root is not None:
        new_soup = BeautifulSoup("<div></div>", "html.parser")
        div = new_soup.find("div")
        assert div is not None
        div.append(content_root)
        soup = new_soup

    # Resolve relative URLs
    if include_links and url:
        url_attributes = [
            "href",
            "src",
            "action",
            "data",
            "poster",
            "background",
            "cite",
            "formaction",
        ]
        for tag in soup.find_all(True):
            for attribute in url_attributes:
                if tag.has_attr(attribute):
                    attr_value = tag[attribute]
                    if isinstance(attr_value, list):
                        continue
                    if attr_value.startswith("//"):
                        tag[attribute] = f"https:{attr_value}"
                    elif not attr_value.startswith(
                        ("http://", "https://", "mailto:", "tel:", "javascript:")
                    ):
                        tag[attribute] = urljoin(url, attr_value)

    # Remove non-content elements by tag
    elements_to_remove = [
        "head",
        "header",
        "footer",
        "script",
        "source",
        "style",
        "svg",
        "iframe",
        "nav",
        "aside",
        "form",
        "noscript",
        "template",
    ]
    if not include_links:
        elements_to_remove.extend(["a", "link"])
    if not include_images:
        elements_to_remove.append("img")
    for element in elements_to_remove:
        for tag in soup.find_all(element):
            tag.decompose()

    # Remove non-content elements by CSS selector
    for selector in _SELECTORS_TO_REMOVE:
        for tag in soup.select(selector):
            tag.decompose()

    # Remove cookie/consent banners
    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", []))
        tag_id = tag.get("id", "") or ""
        if _COOKIE_CONSENT_PATTERN.search(classes) or _COOKIE_CONSENT_PATTERN.search(
            tag_id
        ):
            tag.decompose()

    # Unwrap layout tables
    for table in soup.find_all("table"):
        if _is_layout_table(table):
            table.unwrap()

    # Convert separator elements to <hr>
    for element in soup.find_all(attrs={"role": "separator"}):
        hr = soup.new_tag("hr")
        element.replace_with(hr)
        hr.insert_before(soup.new_string("\n"))
        hr.insert_after(soup.new_string("\n"))

    # Extract language hints and mark code blocks
    for pre in soup.find_all("pre"):
        lang = _extract_code_language(pre)
        if lang:
            marker = soup.new_string(
                f"{_LANG_MARKER_PREFIX}{lang}{_LANG_MARKER_SUFFIX}"
            )
            pre.insert_before(marker)

    result_html = str(soup)

    converter = html2text.HTML2Text()
    converter.ignore_links = not include_links
    converter.ignore_images = not include_images
    converter.body_width = 0
    converter.protect_links = True
    converter.unicode_snob = True
    converter.skip_internal_links = True
    converter.wrap_links = False

    markdown = converter.handle(result_html)

    # Fix up language-annotated fenced code blocks: <!--lang:python-->``` → ```python
    markdown = re.sub(
        rf"{re.escape(_LANG_MARKER_PREFIX)}(\w+){re.escape(_LANG_MARKER_SUFFIX)}\s*```",
        r"```\1",
        markdown,
    )

    # Fix up language-annotated indented code blocks (html2text uses 4-space indent for <pre>)
    # <!--lang:python-->\n    \n    code\n → ```python\ncode\n```
    def _replace_indented_block(m: re.Match) -> str:
        """Convert a language-annotated 4-space-indented block to a fenced code block.

        Used as a ``re.sub`` replacement function. Strips the 4-space or tab
        indentation added by html2text for ``<pre>`` elements, removes leading
        and trailing blank lines within the block, and wraps the result in a
        fenced code block with the extracted language identifier.

        Args:
            m: Match object with group 1 = language identifier, group 2 = indented block.

        Returns:
            Fenced code block string, or an empty string if the block is empty
            after stripping.
        """
        lang = m.group(1)
        block = m.group(2)
        # Strip the 4-space (or tab) indent from every line; drop leading blank lines
        lines = block.rstrip("\n").split("\n")
        dedented = []
        for line in lines:
            if line.startswith("    "):
                dedented.append(line[4:])
            elif line.startswith("\t"):
                dedented.append(line[1:])
            else:
                dedented.append(line.strip())
        # Drop leading/trailing blank lines inside the block
        while dedented and not dedented[0].strip():
            dedented.pop(0)
        while dedented and not dedented[-1].strip():
            dedented.pop()
        if not dedented:
            return ""
        return f"```{lang}\n" + "\n".join(dedented) + "\n```"

    # Match marker followed by all lines starting with whitespace (covers blank indented lines)
    markdown = re.sub(
        rf"{re.escape(_LANG_MARKER_PREFIX)}(\w+){re.escape(_LANG_MARKER_SUFFIX)}\n((?:[ \t][^\n]*\n)*)",
        _replace_indented_block,
        markdown,
    )

    # Remove any remaining orphaned lang markers that didn't match either pattern
    markdown = re.sub(
        rf"{re.escape(_LANG_MARKER_PREFIX)}\w+{re.escape(_LANG_MARKER_SUFFIX)}\n*",
        "",
        markdown,
    )

    return _clean_markdown(markdown)


# ── Fetching ───────────────────────────────────────────────────────────────


def _is_url(s: str) -> bool:
    """Return True when the string looks like an HTTP or HTTPS URL.

    Args:
        s: The string to test.

    Returns:
        True if the scheme is ``http`` or ``https``; False otherwise.
    """
    parsed = urlparse(s)
    return parsed.scheme in ("http", "https")


def _fetch_url(url: str, timeout: int = 10, debug: bool = False) -> str:
    """Fetch a URL and return its response body as a string.

    Sends a GET request with a randomised browser User-Agent to reduce the
    likelihood of bot-detection blocks. Follows redirects automatically.

    Args:
        url: The HTTP or HTTPS URL to fetch.
        timeout: Request timeout in seconds. Defaults to 10.
        debug: When True, prints the URL being fetched to stderr.

    Returns:
        The response body text.

    Raises:
        httpx.HTTPStatusError: If the server returns a 4xx or 5xx status code.
        httpx.RequestError: If a network error occurs (DNS failure, timeout, etc.).
    """
    import random

    os_list = [
        ("Windows NT 10.0", "Win64; x64"),
        ("Macintosh; Apple M2 Mac OS X 14_2_1", "arm64"),
    ]
    os_name, platform = random.choice(os_list)
    ua = f"Mozilla/5.0 ({os_name.split('; ')[0]}; {platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.128 Safari/537.36"

    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    if debug:
        print(f"[html-to-md] fetching {url}", file=sys.stderr)

    with httpx.Client(
        timeout=timeout, follow_redirects=True, headers=headers
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


# ── CLI ────────────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point for the html-to-md CLI.

    Parses command-line arguments, reads HTML from a URL, file, or stdin,
    converts it to clean Markdown via ``_html_to_markdown``, then writes
    the result to stdout or to the file specified by ``--output``.

    Exits with status 1 on HTTP errors, missing input files, or OS I/O errors.
    """
    parser = argparse.ArgumentParser(
        prog="html-to-md",
        description="Convert HTML to clean markdown optimised for LLM consumption.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1].strip()
        if __doc__ and "Examples:" in __doc__
        else "",
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        metavar="FILE|URL|-",
        help="HTML file, URL to fetch, or - for stdin (default: -)",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Write output to FILE instead of stdout",
    )
    parser.add_argument(
        "--url",
        metavar="URL",
        help="Base URL for resolving relative links when input is a file or stdin",
    )
    parser.add_argument(
        "--no-links",
        action="store_true",
        help="Strip all hyperlinks from output",
    )
    parser.add_argument(
        "--include-images",
        action="store_true",
        help="Preserve image references in output",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        metavar="SECS",
        help="HTTP timeout in seconds when fetching a URL (default: 10)",
    )
    parser.add_argument(
        "--debug",
        "-D",
        action="store_true",
        help="Print extra info to stderr",
    )

    args = parser.parse_args()

    # ── Read input ──────────────────────────────────────────────────────────
    base_url: str | None = args.url

    if args.input != "-" and _is_url(args.input):
        # Input is a URL — fetch it
        base_url = base_url or args.input
        try:
            html_content = _fetch_url(
                args.input, timeout=args.timeout, debug=args.debug
            )
        except httpx.HTTPStatusError as e:
            print(
                f"error: HTTP {e.response.status_code} fetching {args.input}",
                file=sys.stderr,
            )
            sys.exit(1)
        except httpx.RequestError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.input == "-":
        # Read from stdin
        if args.debug:
            print("[html-to-md] reading from stdin", file=sys.stderr)
        html_content = sys.stdin.read()
    else:
        # Read from file
        if args.debug:
            print(f"[html-to-md] reading {args.input}", file=sys.stderr)
        try:
            with open(args.input, encoding="utf-8") as fh:
                html_content = fh.read()
        except FileNotFoundError:
            print(f"error: file not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        except OSError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)

    # ── Convert ─────────────────────────────────────────────────────────────
    if args.debug:
        print(
            f"[html-to-md] converting {len(html_content):,} bytes of HTML",
            file=sys.stderr,
        )

    markdown = _html_to_markdown(
        html_content,
        url=base_url,
        include_links=not args.no_links,
        include_images=args.include_images,
    )

    if args.debug:
        print(f"[html-to-md] output: {len(markdown):,} chars", file=sys.stderr)

    # ── Write output ─────────────────────────────────────────────────────────
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(markdown)
            fh.write("\n")
        if args.debug:
            print(f"[html-to-md] written to {args.output}", file=sys.stderr)
    else:
        print(markdown)


if __name__ == "__main__":
    main()
