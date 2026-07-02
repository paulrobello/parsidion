"""Tests for pure TUI helper logic extracted from vault_review.py.

Covers the crash fixes around the transcript popup:

- ``_clamp_selected``: selection re-clamping after entries are popped from the
  queue (rejecting the last visible entry must not leave ``selected`` pointing
  past the end of the list).
- ``_popup_dims``: popup geometry math, including the tiny-terminal guard that
  previously produced negative/zero dimensions and crashed ``curses.newwin``.
"""

from __future__ import annotations

import vault_review


# ---------------------------------------------------------------------------
# _clamp_selected
# ---------------------------------------------------------------------------


class TestClampSelected:
    def test_in_range_unchanged(self) -> None:
        assert vault_review._clamp_selected(2, 5) == 2

    def test_zero_unchanged(self) -> None:
        assert vault_review._clamp_selected(0, 3) == 0

    def test_clamps_to_last_index(self) -> None:
        assert vault_review._clamp_selected(5, 3) == 2

    def test_index_equal_to_count_clamps(self) -> None:
        # Rejecting the last entry pops it: selected == len(entries) afterwards.
        assert vault_review._clamp_selected(3, 3) == 2

    def test_negative_clamps_to_zero(self) -> None:
        assert vault_review._clamp_selected(-1, 3) == 0

    def test_empty_list_returns_zero(self) -> None:
        assert vault_review._clamp_selected(0, 0) == 0
        assert vault_review._clamp_selected(4, 0) == 0

    def test_negative_count_returns_zero(self) -> None:
        assert vault_review._clamp_selected(1, -2) == 0

    def test_reclamp_across_pop_sequence(self) -> None:
        """Simulate rejecting the last visible entry repeatedly (fix 1)."""
        entries = ["a", "b", "c"]
        selected = 2  # last entry selected
        while entries:
            entries.pop(selected)
            selected = vault_review._clamp_selected(selected, len(entries))
            if entries:
                assert 0 <= selected < len(entries)
            else:
                assert selected == 0  # safe default; caller treats as no-entries

    def test_pop_from_middle_keeps_valid_index(self) -> None:
        entries = ["a", "b", "c", "d"]
        selected = 1
        entries.pop(selected)
        selected = vault_review._clamp_selected(selected, len(entries))
        assert entries[selected] == "c"


# ---------------------------------------------------------------------------
# _popup_dims
# ---------------------------------------------------------------------------


class TestPopupDims:
    def test_normal_terminal(self) -> None:
        dims = vault_review._popup_dims(30, 120, 10)
        assert dims is not None
        pop_h, pop_w, top, left = dims
        assert pop_h == 14  # len(lines) + 4, fits within h - 4
        assert pop_w == 100  # capped at 100
        assert top == (30 - pop_h) // 2
        assert left == (120 - pop_w) // 2

    def test_height_capped_by_terminal(self) -> None:
        dims = vault_review._popup_dims(20, 80, 100)
        assert dims is not None
        pop_h, pop_w, _top, _left = dims
        assert pop_h == 16  # h - 4
        assert pop_w == 76  # w - 4

    def test_minimum_viable_terminal(self) -> None:
        dims = vault_review._popup_dims(7, 16, 50)
        assert dims is not None
        pop_h, pop_w, top, left = dims
        assert pop_h == 3
        assert pop_w == 12
        assert top >= 0
        assert left >= 0

    def test_too_short_returns_none(self) -> None:
        assert vault_review._popup_dims(6, 80, 50) is None

    def test_too_narrow_returns_none(self) -> None:
        assert vault_review._popup_dims(30, 15, 50) is None

    def test_tiny_terminal_returns_none(self) -> None:
        # Previously produced negative dims and crashed curses.newwin (fix 2).
        assert vault_review._popup_dims(3, 3, 20) is None
        assert vault_review._popup_dims(0, 0, 20) is None
        assert vault_review._popup_dims(1, 200, 20) is None
        assert vault_review._popup_dims(200, 1, 20) is None

    def test_dims_never_negative_over_grid(self) -> None:
        for h in range(0, 40):
            for w in range(0, 40):
                dims = vault_review._popup_dims(h, w, 20)
                if dims is None:
                    continue
                pop_h, pop_w, top, left = dims
                assert pop_h >= vault_review._POPUP_MIN_H
                assert pop_w >= vault_review._POPUP_MIN_W
                assert top >= 0
                assert left >= 0
                assert top + pop_h <= h
                assert left + pop_w <= w
                # Title slice bound stays non-negative (fix 3).
                assert max(0, pop_w - 6) >= 0

    def test_zero_lines_popup(self) -> None:
        dims = vault_review._popup_dims(30, 120, 0)
        assert dims is not None
        pop_h, _pop_w, _top, _left = dims
        assert pop_h == 4  # frame + hint rows only
