"""Search normalization for the Arabic-first document index.

This only normalizes *index/search keys*. Never apply to document content
or OCR ground truth, where original marks and hamzas must be preserved.
"""

import re
import unicodedata

_ARABIC_MARKS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
_ALEF_VARIANTS = str.maketrans({"آ": "ا", "أ": "ا", "إ": "ا", "ٱ": "ا", "ى": "ي"})


def normalize_arabic_search(text: str) -> str:
    """Casefold, remove Arabic optional marks/tatweel and collapse whitespace.

    Does not normalize ؤ/ئ to avoid merging distinct words aggressively.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _ARABIC_MARKS.sub("", text).replace("ـ", "")
    text = text.translate(_ALEF_VARIANTS)
    return " ".join(text.split())
