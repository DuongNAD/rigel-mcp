"""Text normalisation shared by indexing and querying.

Everything is folded (lowercase, no diacritics, đ -> d) before it reaches SQLite FTS5, so
"da hinh" finds "Đa hình" and documents and queries follow exactly the same rules.
(FTS5's own remove_diacritics does not fold đ, which is why folding happens here.)
"""

from __future__ import annotations

import re
import unicodedata

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Function words only. Ambiguous Vietnamese words are left out on purpose because, once folded,
# they collide with technical terms: "đa" (poly-), "lá" (leaf node), "trọng" (weight),
# "không" (không gian = space), "căn" (căn bậc hai = square root).
STOPWORDS = frozenset(
    """
    a an the is are was were of in on to for from by with and or it this that these those
    as if then than does did how what why when where which who explain describe please about vs versus
    va cua cac nhung mot cho voi nay nao gi hay ve khi se duoc phai thich
    """.split()
)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower().replace("đ", "d"))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", s)


def tokens(s: str) -> list[str]:
    return TOKEN_RE.findall(fold(s))


def query_terms(raw: str) -> list[str]:
    """Distinct, folded, non-trivial terms of a query, in order."""
    out: list[str] = []
    for t in tokens(raw):
        if t in STOPWORDS or t in out or (len(t) < 2 and not t.isdigit()):
            continue
        out.append(t)
    return out


def fts_match(terms: list[str]) -> str:
    """FTS5 MATCH expression: every term OR-ed, plus the exact phrase.

    No prefix matching on purpose: short prefixes ("con*" -> constructor, contract, concrete)
    produced false matches. English inflection is handled by the index's porter stemmer instead.
    """
    if not terms:
        return ""
    parts = [f'"{t}"' for t in terms]
    if len(terms) > 1:
        parts.append('"' + " ".join(terms) + '"')
    return " OR ".join(parts)
