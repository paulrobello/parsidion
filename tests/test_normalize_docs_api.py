"""ARC-104: the docs-api scrub ported out of the Makefile's inline perl.

The acceptance bar for the port is byte-exactness -- CI's ``docs-api-checks``
job regenerates ``docs/api`` and diffs it against the committed tree, so a
single differing byte fails the gate. ``make docs-api`` cannot run from an
agent worktree (typedoc cannot resolve the git remote through a worktree's
``.git`` file indirection), so the strongest available evidence is a
DIFFERENTIAL test: run the original perl one-liner and the Python port over
the same inputs and assert identical bytes. Everything below the differential
test pins individual rules so a failure localises.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "normalize_docs_api", REPO_ROOT / "scripts" / "normalize_docs_api.py"
)
assert _spec is not None and _spec.loader is not None
nda = importlib.util.module_from_spec(_spec)
sys.modules["normalize_docs_api"] = nda
_spec.loader.exec_module(nda)

# The exact expression the Makefile passed to `perl -pi -e` before ARC-104,
# with Make's `$$` un-doubled and the four interpolated paths turned into the
# %ENV lookups the differential harness supplies. Frozen here as the oracle.
PERL_SCRUB = r"""
$ENV{GEN_RESOLVED} ne "" && s|\Q$ENV{GEN_RESOLVED}\E|<repo-root>|g;
$ENV{GEN_ROOT} ne "" && s|\Q$ENV{GEN_ROOT}\E|<repo-root>|g;
$ENV{CURDIR} ne "" && s|\Q$ENV{CURDIR}\E|<repo-root>|g;
$ENV{HOME_RESOLVED} ne "" && s|\Q$ENV{HOME_RESOLVED}\E|<home>|g;
$ENV{GEN_HOME} ne "" && s|\Q$ENV{GEN_HOME}\E|<home>|g;
$ENV{REAL_HOME} ne "" && s|\Q$ENV{REAL_HOME}\E|<home>|g;
s/<input id="[^"]*view-value" class="view-value-toggle-state"[^>]*>\s*//g;
s/<label class="view-value-button pdoc-button" for="[^"]*"><\/label>//g;
s/"default_value": \d+/"default_value": 1/g;
s!\bfrozenset\(\{([^{}]+)\}\)!do { my $i=$1; "frozenset({".join(", ", sort(split(/,\s+/, $i)))."})" }!ge;
s!\{((?:\x27|\&#39;|\&#x27;)[^{}()]*?(?:\x27|\&#39;|\&#x27;)(?:,\s*(?:\x27|\&#39;|\&#x27;)[^{}()]*?(?:\x27|\&#39;|\&#x27;))*)\}!do { my $g=$1; $g =~ /:/ ? "{".$g."}" : "{".join(", ", sort(split(/,\s+/, $g)))."}" }!ge;
"""

GEN_RESOLVED = "/private/tmp/parsidion-docs-gen"
GEN_ROOT = "/tmp/parsidion-docs-gen"
CURDIR = "/Users/someone/Repos/parsidion"
HOME_RESOLVED = "/private/tmp/parsidion-docs-home"
GEN_HOME = "/tmp/parsidion-docs-home"
REAL_HOME = "/Users/someone"

REPO_ROOTS = [GEN_RESOLVED, GEN_ROOT, CURDIR]
HOMES = [HOME_RESOLVED, GEN_HOME, REAL_HOME]

perl_required = pytest.mark.skipif(
    shutil.which("perl") is None, reason="perl not available"
)


def run_perl(data: bytes, tmp_path: Path) -> bytes:
    """Run the frozen perl scrub over *data* and return the rewritten bytes."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "oracle-input"
    target.write_bytes(data)
    env = {
        "GEN_RESOLVED": GEN_RESOLVED,
        "GEN_ROOT": GEN_ROOT,
        "CURDIR": CURDIR,
        "HOME_RESOLVED": HOME_RESOLVED,
        "GEN_HOME": GEN_HOME,
        "REAL_HOME": REAL_HOME,
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(
        ["perl", "-pi", "-e", PERL_SCRUB, str(target)],
        check=True,
        env=env,
        capture_output=True,
    )
    return target.read_bytes()


def run_python(data: bytes) -> bytes:
    """Run the ported scrub over *data*."""
    return nda.scrub_bytes(data, nda.build_scrub_rules(REPO_ROOTS, HOMES))


# --- fixture corpus ---------------------------------------------------------

FIXTURES: dict[str, bytes] = {
    "resolved_path_before_literal": (
        f"<span>PosixPath(&#39;{GEN_RESOLVED}/skills/parsidion/scripts&#39;)</span>\n"
        f"<span>{GEN_ROOT}/installer</span>\n"
        f"<span>{CURDIR}/Makefile</span>\n"
    ).encode(),
    "home_needles": (
        f"<span>{HOME_RESOLVED}/ParsidionVault</span>\n"
        f"<span>{GEN_HOME}/.claude</span>\n"
        f"<span>{REAL_HOME}/Repos</span>\n"
    ).encode(),
    "frozenset_unsorted": (
        b"<span>frozenset({'zeta', 'alpha', 'Mid', 'beta'})</span>\n"
        b"<span>frozenset({'only'})</span>\n"
    ),
    "frozenset_html_entities": (
        b"<span>frozenset({&#39;zulu&#39;, &#39;alpha&#39;})</span>\n"
        b"<span>frozenset({&#x27;yankee&#x27;, &#x27;bravo&#x27;})</span>\n"
    ),
    "set_display_unsorted": (
        b"<span>{'.obsidian', 'Templates', '.git', '.trash'}</span>\n"
        b"<span>{&#39;zz&#39;, &#39;aa&#39;}</span>\n"
    ),
    "dict_display_untouched": (
        b"<span>{'zeta': 1, 'alpha': 2}</span>\n"
        b"<span>{&#39;b&#39;: &#39;x&#39;, &#39;a&#39;: &#39;y&#39;}</span>\n"
    ),
    "toggle_markup": (
        b'<input id="foo-view-value" class="view-value-toggle-state" type="checkbox" '
        b'aria-hidden="true" tabindex="-1">\n'
        b'<label class="view-value-button pdoc-button" for="foo-view-value"></label>\n'
        b"<span>kept</span>\n"
    ),
    "toggle_at_end_of_line_eats_newline": (
        b'a<input id="x-view-value" class="view-value-toggle-state" type="checkbox">\n'
        b"b\n"
    ),
    "default_value_field_length": (
        b'{"default_value": 17, "other": 3}\n{"default_value": 1}\n'
    ),
    # perl's $_ is one record, so a display split across lines is NOT matched.
    # A whole-file re.sub WOULD match it; this fixture pins the difference.
    "multiline_frozenset_not_matched": (
        b"<span>frozenset({'zeta',\n'alpha'})</span>\n"
    ),
    "multiline_brace_group_not_matched": (b"<span>{'zeta',\n'alpha'}</span>\n"),
    # Records are split on \n only -- \r and \f must not act as separators.
    "cr_and_formfeed_are_not_separators": (
        b"<span>{'b', 'a'}\r{'d', 'c'}</span>\n<span>{'f', 'e'}\x0c{'h', 'g'}</span>\n"
    ),
    "no_trailing_newline": b"<span>{'b', 'a'}</span>",
    "empty": b"",
    "already_scrubbed_is_a_noop": (
        b"<span>&lt;repo-root&gt;/installer</span>\n"
        b"<span>frozenset({'alpha', 'zeta'})</span>\n"
        b'{"default_value": 1}\n'
    ),
    "wide_separator_split": (b"<span>{'zulu',    'alpha',\t'mike'}</span>\n"),
}


@perl_required
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_port_matches_perl_on_fixtures(name: str, tmp_path: Path) -> None:
    """Each synthetic fixture scrubs to identical bytes under perl and Python."""
    data = FIXTURES[name]
    assert run_python(data) == run_perl(data, tmp_path)


@perl_required
def test_port_matches_perl_on_committed_docs(tmp_path: Path) -> None:
    """Real generated artifacts scrub identically (and mostly no-op) under both.

    Already-scrubbed committed output is the over-matching detector: any rule
    that fires where perl's did not shows up as a byte difference here.
    """
    docs_api = REPO_ROOT / "docs" / "api"
    if not docs_api.is_dir():
        pytest.skip("docs/api is not present in this checkout")
    candidates = [
        p
        for p in sorted(docs_api.rglob("*"))
        if p.is_file() and p.suffix in nda.SCRUB_SUFFIXES
    ]
    if not candidates:
        pytest.skip("no scrubbable files under docs/api")
    # Bound the corpus: a spread of the largest files plus the search indexes.
    sample = sorted(candidates, key=lambda p: p.stat().st_size, reverse=True)[:12]
    for i, path in enumerate(sample):
        data = path.read_bytes()
        assert run_python(data) == run_perl(data, tmp_path / str(i)), path


@perl_required
def test_port_matches_perl_on_dirtied_docs(tmp_path: Path) -> None:
    """A real pdoc page with pre-scrub content injected scrubs identically.

    The committed tree is already scrubbed, so on its own it only proves the
    port does not OVER-match. This re-dirties a real page with the machine
    paths and unsorted displays the scrub exists to remove, exercising every
    rule against realistic surrounding markup.
    """
    docs_api = REPO_ROOT / "docs" / "api"
    page = docs_api / "python" / "core" / "vault_path.html"
    if not page.is_file():
        pytest.skip("docs/api/python/core/vault_path.html is not present")
    dirt = (
        f'<span class="default_value">PosixPath(&#39;{GEN_RESOLVED}/x&#39;)</span>\n'
        f'<span class="default_value">PosixPath(&#39;{GEN_ROOT}/y&#39;)</span>\n'
        f'<span class="default_value">{CURDIR}/Makefile</span>\n'
        f'<span class="default_value">{HOME_RESOLVED}/v</span>\n'
        f'<span class="default_value">{GEN_HOME}/w</span>\n'
        f'<span class="default_value">{REAL_HOME}/Repos</span>\n'
        "<span>frozenset({&#39;zulu&#39;, &#39;alpha&#39;, &#39;mike&#39;})</span>\n"
        "<span>{&#39;zz&#39;, &#39;aa&#39;}</span>\n"
        "<span>{&#39;zz&#39;: 1, &#39;aa&#39;: 2}</span>\n"
        '<input id="q-view-value" class="view-value-toggle-state" type="checkbox">\n'
        '<label class="view-value-button pdoc-button" for="q-view-value"></label>\n'
        '{"default_value": 23}\n'
    ).encode()
    data = page.read_bytes() + dirt
    scrubbed = run_python(data)
    assert scrubbed == run_perl(data, tmp_path)
    # Sanity: the dirt really was removed, so the comparison is not vacuous.
    assert GEN_RESOLVED.encode() not in scrubbed
    assert REAL_HOME.encode() not in scrubbed
    assert b"frozenset({&#39;alpha&#39;, &#39;mike&#39;, &#39;zulu&#39;})" in scrubbed
    assert b"{&#39;aa&#39;, &#39;zz&#39;}" in scrubbed
    assert b"{&#39;zz&#39;: 1, &#39;aa&#39;: 2}" in scrubbed
    # The page's own stylesheet mentions the class, so pin the injected tags.
    assert b'id="q-view-value"' not in scrubbed
    assert b'for="q-view-value"' not in scrubbed
    assert b'"default_value": 23' not in scrubbed


# --- per-rule pins ----------------------------------------------------------


def test_resolved_needle_wins_over_literal() -> None:
    """The /private form must be rewritten before the bare /tmp form.

    Reversing the order leaves '/private<repo-root>' -- the recorded incident.
    """
    out = run_python(f"x {GEN_RESOLVED}/a y\n".encode())
    assert out == b"x <repo-root>/a y\n"
    assert b"/private" not in out


def test_frozenset_elements_are_sorted() -> None:
    assert (
        run_python(b"frozenset({'zeta', 'alpha'})\n")
        == b"frozenset({'alpha', 'zeta'})\n"
    )


def test_brace_group_sorted_but_dict_untouched() -> None:
    assert run_python(b"{'zz', 'aa'}\n") == b"{'aa', 'zz'}\n"
    assert run_python(b"{'zz': 1, 'aa': 2}\n") == b"{'zz': 1, 'aa': 2}\n"


def test_default_value_lengths_pinned() -> None:
    assert run_python(b'"default_value": 42\n') == b'"default_value": 1\n'


def test_toggle_markup_stripped() -> None:
    src = (
        b'<input id="a-view-value" class="view-value-toggle-state" type="checkbox">'
        b'<label class="view-value-button pdoc-button" for="a-view-value"></label>'
        b"tail\n"
    )
    assert run_python(src) == b"tail\n"


def test_multiline_display_is_left_alone() -> None:
    """Records are lines: a display spanning a newline is not a match."""
    src = b"frozenset({'zeta',\n'alpha'})\n"
    assert run_python(src) == src


@pytest.mark.parametrize(
    "repo_roots,homes",
    [([""], ["/home"]), (["/root"], [""]), ([], [""])],
)
def test_empty_needle_is_a_hard_error(repo_roots: list[str], homes: list[str]) -> None:
    """An empty needle must fail loudly, never silently skip the rule."""
    with pytest.raises(ValueError, match="empty path needle"):
        nda.build_scrub_rules(repo_roots, homes)


def test_cli_rejects_empty_needle(tmp_path: Path) -> None:
    """The CLI surfaces the empty-needle error as exit code 2."""
    rc = nda.main(["normalize_docs_api.py", str(tmp_path), "--repo-root", ""])
    assert rc == 2


def test_cli_scrubs_html_and_js_only(tmp_path: Path) -> None:
    """scrub_tree visits *.html and *.js, matching the recipe's find predicate."""
    (tmp_path / "sub").mkdir()
    html = tmp_path / "sub" / "a.html"
    js = tmp_path / "b.js"
    other = tmp_path / "c.txt"
    payload = f"{CURDIR}/x\n".encode()
    for target in (html, js, other):
        target.write_bytes(payload)

    rc = nda.main(
        [
            "normalize_docs_api.py",
            str(tmp_path),
            "--repo-root",
            CURDIR,
            "--home",
            REAL_HOME,
        ]
    )
    assert rc == 0
    assert html.read_bytes() == b"<repo-root>/x\n"
    assert js.read_bytes() == b"<repo-root>/x\n"
    assert other.read_bytes() == payload


def test_cli_without_needles_skips_the_scrub(tmp_path: Path) -> None:
    """Needle-free invocation keeps the pre-ARC-104 behaviour (canonicalize only)."""
    html = tmp_path / "a.html"
    payload = f"{CURDIR}/x\n".encode()
    html.write_bytes(payload)
    assert nda.main(["normalize_docs_api.py", str(tmp_path)]) == 0
    assert html.read_bytes() == payload
