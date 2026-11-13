"""Two things the quarantined reader's defects (2026-11-13) leave to pin: what the shared text cleaner removes, and the
auto levels a trash depends on (a sentence of THREAT-MODEL.md)."""

from __future__ import annotations

import pytest

from clara.connectors.mail import clean_line, clean_text
from clara.gate.actions import CLASSES, LEVEL_KEYS, RAISED_WHEN_TAINTED

# what the cleaner has always removed
OLD = ["\x00", "\x1f", "\x7f", "­", "​", "‎", "‏", "‪", "‮", "⁠", "⁤", "⁦",
       "⁩", "﻿", "\U000e0041", "\U000e007f"]
# what it now removes: the C1 controls, the fillers that draw as blanks, the Arabic letter mark, the Mongolian selectors,
# the format characters left of the 2060 block, the variation selectors but U+FE0F, the specials, the rest of plane 14
ADDED = [chr(code) for code in range(0x80, 0xA0)] + [
    "؜", "ᅟ", "ᅠ", "឴", "឵", "᠋", "᠌", "᠍", "᠎", "᠏", "⁥", "⁪",
    "⁯", "ㅤ", "︀", "︍", "︎", "ﾠ", "￰", "￸", "\U000e0080", "\U000e0100", "\U000e01ef",
    "\U000e0fff",
]
# real text: accents, CJK (an ideograph and a Hangul syllable), Arabic with a ZWNJ, Hebrew points, Devanagari with a ZWJ,
# Thai, emoji sequences (ZWJ, U+FE0F, a keycap, a skin tone), tab and line breaks, the NBSP
REAL = [
    "Réservé à l'équipe : café, naïveté, œuvre, Œil, Noël, ça va",
    "日本語のテキスト、漢字かな交じり文。한국어",
    "می‌خواهم العربية",
    "שָׁלוֹם",
    "क्‍ष क्‌ष",
    "ภาษาไทย",
    "\U0001f468‍\U0001f469‍\U0001f467 ❤️ 1️⃣ \U0001f44d\U0001f3fd",
    "a\tb\nc d",
]


@pytest.mark.parametrize("char", OLD + ADDED, ids=lambda char: f"U+{ord(char):04X}")
def test_the_cleaner_removes_what_a_screen_does_not_show(char):
    assert clean_text(f"a{char}b") == "ab" and clean_line(f"a{char}b") == "ab"


@pytest.mark.parametrize("text", REAL)
def test_the_cleaner_leaves_real_text_alone(text):
    assert clean_text(text) == text
    assert clean_line(text) == " ".join(text.split())


def test_a_variant_of_a_character_loses_its_selector_and_keeps_the_character():
    assert clean_text("葛\U000e0100城") == "葛城"


# docs/THREAT-MODEL.md says that a trash (`mail_trash`) asks at level 3, runs with no card from level 4 even in a turn that read
# untrusted content, and is not raised by one (the owner's choice: a trash can be undone from the Trash). If the ladder or the
# raises change and this fails, revise the sentences about a trash in the rows « Content Clara reads » and « An email » of that
# document (and the entry in docs/DECISIONS.md), then these numbers.
def test_a_trash_asks_from_level_3_and_runs_from_level_4_and_a_tainted_turn_does_not_raise_it():
    asks, runs, _ = CLASSES["mail_trash"]
    assert (LEVEL_KEYS.index(asks), LEVEL_KEYS.index(runs)) == (3, 4)
    assert "mail_trash" not in RAISED_WHEN_TAINTED
