#!/usr/bin/env python3
"""vault-export — export vault notes to different formats.

Modes (mutually exclusive; default is --list):
    --html [OUTPUT_DIR]     Export notes as static HTML files
    --zip [OUTPUT_FILE]     Zip export of .md files
    --list                  List what would be exported (default)

Filters (all modes):
    --project PROJECT       Only export notes for this project
    --folder FOLDER         Only export notes from this folder
    --tag TAG               Only export notes with this tag
"""

import argparse
import html
import os
import re
import sys
import zipfile
from pathlib import Path

import vault_common

# ---------------------------------------------------------------------------
# Note filtering
# ---------------------------------------------------------------------------


def _collect_notes(
    project: str | None,
    folder: str | None,
    tag: str | None,
    vault_path: Path,
) -> list[Path]:
    """Collect vault notes matching the given filters.

    Uses the note_index DB when available, falls back to a file walk.

    Args:
        project: Only include notes with this project field.
        folder: Only include notes whose vault folder matches this name.
        tag: Only include notes containing this tag.
        vault_path: Path to the vault root.

    Returns:
        Sorted list of matching note paths.
    """
    # Try DB-backed filtering first
    db_results = vault_common.query_note_index(
        project=project,
        folder=folder,
        tag=tag,
    )
    if db_results is not None:
        return sorted(db_results)

    # Fallback: walk all vault notes and filter by frontmatter
    candidates: list[Path] = []
    for path in vault_common.all_vault_notes(vault=vault_path):
        # Folder filter
        if folder is not None:
            rel = path.relative_to(vault_path)
            parts = rel.parts
            note_folder = parts[0] if len(parts) > 1 else ""
            if note_folder.lower() != folder.lower():
                continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = vault_common.parse_frontmatter(content)
        # Project filter
        if project is not None:
            if fm.get("project", "").lower() != project.lower():
                continue
        # Tag filter
        if tag is not None:
            raw_tags = fm.get("tags", [])
            if isinstance(raw_tags, str):
                note_tags = [t.strip() for t in raw_tags.split(",")]
            else:
                note_tags = [str(t).strip() for t in raw_tags]
            if tag.lower() not in [t.lower() for t in note_tags]:
                continue
        candidates.append(path)
    return sorted(candidates)


# ---------------------------------------------------------------------------
# Markdown → HTML renderer (stdlib-only, regex-based)
# ---------------------------------------------------------------------------

_RE_H1 = re.compile(r"^# (.+)$", re.MULTILINE)
_RE_H2 = re.compile(r"^## (.+)$", re.MULTILINE)
_RE_H3 = re.compile(r"^### (.+)$", re.MULTILINE)
_RE_H4 = re.compile(r"^#### (.+)$", re.MULTILINE)
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RE_ITALIC = re.compile(r"\*(.+?)\*")
_RE_CODE_INLINE = re.compile(r"`([^`]+)`")
_RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_RE_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_RE_HR = re.compile(r"^---+$", re.MULTILINE)
_RE_UL = re.compile(r"^[-*] (.+)$", re.MULTILINE)
_RE_OL = re.compile(r"^\d+\. (.+)$", re.MULTILINE)
_RE_BLOCKQUOTE = re.compile(r"^> (.+)$", re.MULTILINE)
_RE_CODE_FENCE_OPEN = re.compile(r"^```(\w*)$")
_RE_CODE_FENCE_CLOSE = re.compile(r"^```$")


def _md_to_html(md: str) -> str:
    """Convert Markdown text to basic HTML using regex transformations.

    Handles headings, bold, italic, inline code, fenced code blocks,
    links, wikilinks, horizontal rules, blockquotes, and unordered/ordered
    lists. Does not handle nested lists or tables.

    Args:
        md: Markdown source text.

    Returns:
        HTML string with a ``<div class="content">`` wrapper.
    """
    lines = md.splitlines()
    out_lines: list[str] = []
    in_code_block = False
    code_lang = ""
    code_lines: list[str] = []
    in_ul = False
    in_ol = False

    def _flush_list() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out_lines.append("</ul>")
            in_ul = False
        if in_ol:
            out_lines.append("</ol>")
            in_ol = False

    def _inline(text: str) -> str:
        """Apply inline transformations to a line of text."""
        text = html.escape(text)
        text = _RE_CODE_INLINE.sub(r"<code>\1</code>", text)
        text = _RE_BOLD.sub(r"<strong>\1</strong>", text)
        text = _RE_ITALIC.sub(r"<em>\1</em>", text)
        text = _RE_LINK.sub(r'<a href="\2">\1</a>', text)
        text = _RE_WIKILINK.sub(r'<span class="wikilink">\1</span>', text)
        return text

    for line in lines:
        # Fenced code block handling
        if in_code_block:
            if _RE_CODE_FENCE_CLOSE.match(line):
                in_code_block = False
                lang_class = f' class="language-{code_lang}"' if code_lang else ""
                code_content = html.escape("\n".join(code_lines))
                out_lines.append(f"<pre><code{lang_class}>{code_content}</code></pre>")
                code_lines = []
                code_lang = ""
            else:
                code_lines.append(line)
            continue

        m_open = _RE_CODE_FENCE_OPEN.match(line)
        if m_open:
            _flush_list()
            in_code_block = True
            code_lang = m_open.group(1)
            continue

        # Headings
        if line.startswith("#### "):
            _flush_list()
            out_lines.append(f"<h4>{_inline(line[5:])}</h4>")
            continue
        if line.startswith("### "):
            _flush_list()
            out_lines.append(f"<h3>{_inline(line[4:])}</h3>")
            continue
        if line.startswith("## "):
            _flush_list()
            out_lines.append(f"<h2>{_inline(line[3:])}</h2>")
            continue
        if line.startswith("# "):
            _flush_list()
            out_lines.append(f"<h1>{_inline(line[2:])}</h1>")
            continue

        # Horizontal rule
        if re.match(r"^---+$", line):
            _flush_list()
            out_lines.append("<hr>")
            continue

        # Blockquote
        if line.startswith("> "):
            _flush_list()
            out_lines.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
            continue

        # Unordered list
        if re.match(r"^[-*] ", line):
            if in_ol:
                out_lines.append("</ol>")
                in_ol = False
            if not in_ul:
                out_lines.append("<ul>")
                in_ul = True
            out_lines.append(f"<li>{_inline(line[2:])}</li>")
            continue

        # Ordered list
        m_ol = re.match(r"^\d+\. (.+)$", line)
        if m_ol:
            if in_ul:
                out_lines.append("</ul>")
                in_ul = False
            if not in_ol:
                out_lines.append("<ol>")
                in_ol = True
            out_lines.append(f"<li>{_inline(m_ol.group(1))}</li>")
            continue

        # Blank line
        if not line.strip():
            _flush_list()
            out_lines.append("")
            continue

        # Paragraph
        _flush_list()
        out_lines.append(f"<p>{_inline(line)}</p>")

    _flush_list()
    return '<div class="content">\n' + "\n".join(out_lines) + "\n</div>"


_HTML_PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{ font-family: Georgia, serif; max-width: 860px; margin: 2rem auto; padding: 0 1rem; color: #222; background: #fafafa; }}
  h1, h2, h3, h4 {{ font-family: 'Trebuchet MS', sans-serif; }}
  pre {{ background: #f4f4f4; padding: 1rem; overflow-x: auto; border-radius: 4px; }}
  code {{ font-family: 'Courier New', monospace; font-size: 0.9em; }}
  blockquote {{ border-left: 4px solid #ccc; margin: 0; padding-left: 1rem; color: #555; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 1.5rem 0; }}
  .wikilink {{ color: #555; font-style: italic; }}
  a {{ color: #0066cc; }}
  .meta {{ color: #888; font-size: 0.85em; margin-bottom: 1.5rem; }}
  nav {{ margin-bottom: 1rem; }}
  nav a {{ color: #0066cc; text-decoration: none; }}
</style>
</head>
<body>
<nav><a href="index.html">← Index</a></nav>
<div class="meta">{meta}</div>
{body}
</body>
</html>
"""

_HTML_INDEX_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vault Export — Index</title>
<style>
  body {{ font-family: 'Trebuchet MS', sans-serif; max-width: 860px; margin: 2rem auto; padding: 0 1rem; color: #222; background: #fafafa; }}
  h1 {{ border-bottom: 2px solid #ddd; padding-bottom: 0.5rem; }}
  ul {{ list-style: disc; padding-left: 1.5rem; }}
  li {{ margin: 0.25rem 0; }}
  a {{ color: #0066cc; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .meta {{ color: #888; font-size: 0.8em; }}
</style>
</head>
<body>
<h1>Vault Export</h1>
<p class="meta">Exported {count} notes.</p>
<ul>
{links}
</ul>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Export modes
# ---------------------------------------------------------------------------


def _cmd_list(
    project: str | None,
    folder: str | None,
    tag: str | None,
    vault_path: Path,
) -> None:
    """List notes that would be exported without performing export.

    Args:
        project: Project filter.
        folder: Folder filter.
        tag: Tag filter.
        vault_path: Path to the vault root.
    """
    notes = _collect_notes(
        project=project, folder=folder, tag=tag, vault_path=vault_path
    )
    if not notes:
        print("No notes match the given filters.")
        return
    print(f"Would export {len(notes)} note(s):\n")
    for path in notes:
        rel = path.relative_to(vault_path)
        print(f"  {rel}")


def _cmd_html(
    output_dir: Path,
    project: str | None,
    folder: str | None,
    tag: str | None,
    vault_path: Path,
) -> None:
    """Export vault notes as static HTML files.

    Creates one HTML file per note plus an index.html in OUTPUT_DIR.

    Args:
        output_dir: Destination directory (created if absent).
        project: Project filter.
        folder: Folder filter.
        tag: Tag filter.
        vault_path: Path to the vault root.
    """
    notes = _collect_notes(
        project=project, folder=folder, tag=tag, vault_path=vault_path
    )
    if not notes:
        print("No notes match the given filters.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    exported: list[tuple[str, str]] = []  # (html_filename, title)
    for path in notes:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"  Warning: cannot read {path}: {exc}", file=sys.stderr)
            continue

        fm = vault_common.parse_frontmatter(content)
        body_md = vault_common.get_body(content)
        title = vault_common.extract_title(content, path.stem)

        # Build meta line
        meta_parts: list[str] = []
        if fm.get("date"):
            meta_parts.append(fm["date"])
        if fm.get("type"):
            meta_parts.append(fm["type"])
        if fm.get("project"):
            meta_parts.append(f"project: {fm['project']}")
        raw_tags = fm.get("tags", [])
        if isinstance(raw_tags, list) and raw_tags:
            meta_parts.append("tags: " + ", ".join(str(t) for t in raw_tags))
        elif isinstance(raw_tags, str) and raw_tags:
            meta_parts.append(f"tags: {raw_tags}")
        meta_str = " · ".join(meta_parts)

        body_html = _md_to_html(body_md)
        html_filename = path.stem + ".html"
        page_html = _HTML_PAGE_TEMPLATE.format(
            title=html.escape(title),
            meta=html.escape(meta_str),
            body=body_html,
        )
        out_path = output_dir / html_filename
        out_path.write_text(page_html, encoding="utf-8")
        exported.append((html_filename, title))

    # Write index.html
    link_items = "\n".join(
        f'  <li><a href="{html.escape(fname)}">{html.escape(t)}</a></li>'
        for fname, t in sorted(exported, key=lambda x: x[1].lower())
    )
    index_html = _HTML_INDEX_TEMPLATE.format(
        count=len(exported),
        links=link_items,
    )
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")

    print(f"Exported {len(exported)} note(s) to {output_dir}/")
    print(f"  Open: {output_dir / 'index.html'}")


def _cmd_zip(
    output_file: Path,
    project: str | None,
    folder: str | None,
    tag: str | None,
    vault_path: Path,
) -> None:
    """Export vault notes as a ZIP archive of .md files.

    Args:
        output_file: Path for the output .zip file.
        project: Project filter.
        folder: Folder filter.
        tag: Tag filter.
        vault_path: Path to the vault root.
    """
    notes = _collect_notes(
        project=project, folder=folder, tag=tag, vault_path=vault_path
    )
    if not notes:
        print("No notes match the given filters.")
        return

    count = 0
    with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in notes:
            try:
                rel = path.relative_to(vault_path)
            except ValueError:
                rel = path.name  # type: ignore[assignment]
            zf.write(path, arcname=str(rel))
            count += 1

    print(f"Zipped {count} note(s) to {output_file}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate export command.

    Raises:
        SystemExit: On invalid arguments or after completion.
    """
    parser = argparse.ArgumentParser(
        prog="vault-export",
        description="Export vault notes to HTML or ZIP.",
    )

    parser.add_argument(
        "--vault",
        "-V",
        metavar="VAULT",
        default=None,
        help="Use a specific vault (path or named vault).",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--html",
        metavar="OUTPUT_DIR",
        nargs="?",
        const="./vault-export",
        default=None,
        help="Export notes as static HTML (default dir: ./vault-export/).",
    )
    mode_group.add_argument(
        "--zip",
        metavar="OUTPUT_FILE",
        nargs="?",
        const="vault-export.zip",
        default=None,
        help="Export notes as a ZIP archive (default: vault-export.zip).",
    )
    mode_group.add_argument(
        "--list",
        action="store_true",
        help="List notes that would be exported (default mode).",
    )

    parser.add_argument(
        "--project",
        metavar="PROJECT",
        default=None,
        help="Only export notes for this project.",
    )
    parser.add_argument(
        "--folder",
        metavar="FOLDER",
        default=None,
        help="Only export notes from this vault folder.",
    )
    parser.add_argument(
        "--tag",
        metavar="TAG",
        default=None,
        help="Only export notes with this tag.",
    )

    args = parser.parse_args()

    # Resolve vault path
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())

    # QA-001: Replace module-level VAULT_ROOT with try/finally restore pattern
    original_vault_root = vault_common.VAULT_ROOT
    vault_common.VAULT_ROOT = vault_path

    try:
        if args.html is not None:
            _cmd_html(
                Path(args.html),
                project=args.project,
                folder=args.folder,
                tag=args.tag,
                vault_path=vault_path,
            )
        elif args.zip is not None:
            _cmd_zip(
                Path(args.zip),
                project=args.project,
                folder=args.folder,
                tag=args.tag,
                vault_path=vault_path,
            )
        else:
            # Default: --list
            _cmd_list(
                project=args.project,
                folder=args.folder,
                tag=args.tag,
                vault_path=vault_path,
            )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(0)
    finally:
        vault_common.VAULT_ROOT = original_vault_root


if __name__ == "__main__":
    main()
