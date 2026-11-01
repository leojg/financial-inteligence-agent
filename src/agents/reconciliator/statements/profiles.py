"""Per-bank statement profiles: data that adapts the table reader to one institution's layout.

The generic reader already finds columns by their header labels, so most
statements need no profile. A profile is for what the generic reader cannot
know: a label the vocabulary lacks, lines to skip, the sign convention of a
single amount column, the decimal separator.

Profiles are written from real statements, never from guesses. To add one,
run the private eval on that bank's statements (``make eval-private``), look at
the diagnostics of the ones that do not close, and add the smallest profile that
fixes them, with a test built from a redacted copy of the layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from shared.models import AccountType
from shared.parsing import DecimalSeparator, fold


@dataclass(frozen=True)
class BankProfile:
    """Overrides for one institution's statement layout."""

    name: str
    detect: str  # regex matched against the folded text of the first page
    institution: str | None = None
    account_type: AccountType | None = None
    # Column kind → extra labels (folded: upper case, no accents) the vocabulary lacks.
    labels: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Regexes on folded line text: lines that are never movements.
    skip_lines: tuple[str, ...] = ()
    decimal: DecimalSeparator | None = None
    # How to read an unsigned amount in a single amount column: as money in
    # ("credit") or as money out / a charge ("debit"). None: by account type.
    unsigned_amount_is: Literal["credit", "debit"] | None = None


GENERIC = BankProfile(name="generic", detect=r".")

# Real profiles go here (see the module docstring). Order matters: first match wins.
PROFILES: list[BankProfile] = []


def detect_profile(
    first_page_text: str, profiles: list[BankProfile] | None = None
) -> BankProfile:
    """Return the first profile whose pattern matches the first page, or the generic one."""
    text = fold(first_page_text)
    for profile in PROFILES if profiles is None else profiles:
        if re.search(profile.detect, text):
            return profile
    return GENERIC
