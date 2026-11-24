from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

HOMOGLYPH = {
    "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
    "o": "о", "p": "р", "t": "т", "x": "х", "y": "у", "u": "и", "n": "п",
    "3": "з", "0": "о", "6": "б", "4": "ч", "1": "и", "5": "з", "9": "я", "7": "т", "@": "а",
    "$": "с", "*": "", "-": "", "_": "", ".": "", ",": "", "'": "", "`": "",
}
_KEEP = re.compile(r"[^a-zа-яё0-9]+")
_REPEAT = re.compile(r"(.)\1{1,}")

LATIN = re.compile(r"[a-z]")
CYR = re.compile(r"[а-яё]")


def basic(word: str) -> str:
    text = unicodedata.normalize("NFKC", word or "").lower().replace("ё", "е")
    return _KEEP.sub("", text)


def to_cyr(word: str) -> str:
    return "".join(HOMOGLYPH.get(ch, ch) for ch in word)


def collapse(word: str) -> str:
    return _REPEAT.sub(r"\1", word)


def variants(word: str) -> list:
    plain = basic(word)
    if not plain:
        return []
    out = {plain, collapse(plain)}
    cyr = to_cyr(plain)
    if cyr and cyr != plain:
        out.add(cyr)
        out.add(collapse(cyr))
    return [v for v in out if v]


def is_latin(text: str) -> bool:
    return bool(LATIN.search(text)) and not CYR.search(text)


def read_text(path) -> str:
    for name in ("utf-8-sig", "cp1251"):
        try:
            with open(path, "r", encoding=name) as handle:
                return handle.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


PREFIXES = [
    "недо", "пере", "через", "чрез", "пред", "разо", "подо", "надо", "безо",
    "рас", "раз", "рос", "роз", "без", "бес", "воз", "вос", "изо", "ото",
    "при", "про", "под", "над", "обо", "объ", "подъ", "разъ", "отъ", "изъ",
    "съе", "въе", "за", "по", "на", "вы", "от", "до", "об", "из", "ис",
    "со", "вз", "вс", "не", "ни", "у", "о", "с", "в", "а",
]
_PREFIX_RE = "|".join(sorted(PREFIXES, key=len, reverse=True))

ENDINGS = ["", "а", "е", "и", "о", "у", "ы", "ю", "я",
           "ам", "ах", "ев", "ей", "ем", "ой", "ом", "ов", "ям", "ях",
           "ами", "ями"]
_ENDING_RE = "(?:%s)" % "|".join(sorted(ENDINGS, key=len, reverse=True))

GLUE_MAX_GAP = 0.12
GLUE_MAX_PART = 3
GLUE_MIN_LEN = 4


@dataclass
class Hit:
    start: float
    end: float
    text: str
    rule: str
    prob: float = 1.0


class Rule:
    __slots__ = ("raw", "kind", "pat", "tokens")

    def __init__(self, raw: str):
        self.raw = raw.strip()
        self.tokens: List["Rule"] = []
        self.pat: Optional[re.Pattern] = None
        self.kind = "stem"
        text = self.raw
        if text.startswith("re:"):
            self.kind = "re"
            self.pat = re.compile(text[3:], re.IGNORECASE)
        elif " " in text:
            self.kind = "phrase"
            self.tokens = [Rule(tok) for tok in text.split()]
        elif text.startswith("="):
            self.kind = "exact"
            self.pat = re.compile("^" + re.escape(basic(text[1:])) + "$")
        elif text.startswith("~"):
            self.kind = "form"
            self.pat = re.compile("^" + re.escape(basic(text[1:])) + _ENDING_RE + "$")
        else:
            stem = basic(text)
            if is_latin(stem):
                self.pat = re.compile("^" + re.escape(stem))
            else:
                self.pat = re.compile(f"^(?:{_PREFIX_RE})?" + re.escape(stem))

    def match_word(self, forms: Sequence[str]) -> bool:
        if self.kind == "phrase" or self.pat is None:
            return False
        return any(self.pat.search(v) for v in forms)

    def __repr__(self) -> str:
        return "Rule(%r)" % self.raw


def read_wordlist(path: Path) -> List[str]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip() if not line.strip().startswith("re:") else line.strip()
        if line:
            out.append(line)
    return out


class Matcher:
    def __init__(self, deny: Sequence[str], allow: Sequence[str] = ()):
        self.rules = [Rule(r) for r in dict.fromkeys(deny)]
        self.singles = [r for r in self.rules if r.kind != "phrase"]
        self.phrases = [r for r in self.rules if r.kind == "phrase"]
        self.whitelist = [r for r in (Rule(w) for w in allow) if r.kind != "phrase"]

    def is_allowed(self, forms: Sequence[str]) -> bool:
        return any(r.match_word(forms) for r in self.whitelist)

    def check_variants(self, forms: Sequence[str]) -> Optional[str]:
        if not forms:
            return None
        for rule in self.singles:
            if rule.match_word(forms):
                return None if self.is_allowed(forms) else rule.raw
        return None

    def check(self, word: str) -> Optional[str]:
        return self.check_variants(variants(word))

    def scan(self, words: Sequence) -> List[Hit]:
        hits: List[Hit] = []
        cache = [variants(w.text) for w in words]
        hit_at = [False] * len(words)
        for i, word in enumerate(words):
            rule = self.check_variants(cache[i])
            if rule:
                hit_at[i] = True
                hits.append(Hit(word.start, word.end, word.text, rule, _prob(word)))
        hits += self._glued(words, hit_at)
        for phrase in self.phrases:
            count = len(phrase.tokens)
            if count < 2:
                continue
            for i in range(len(words) - count + 1):
                ok = True
                for k, token in enumerate(phrase.tokens):
                    forms = cache[i + k]
                    if not forms or self.is_allowed(forms) or not token.match_word(forms):
                        ok = False
                        break
                if ok:
                    hits.append(Hit(words[i].start, words[i + count - 1].end,
                                    " ".join(words[i + k].text for k in range(count)),
                                    phrase.raw,
                                    min(_prob(words[i + k]) for k in range(count))))
        hits.sort(key=lambda h: h.start)
        return hits

    def _glued(self, words: Sequence, hit_at: List[bool]) -> List[Hit]:
        out: List[Hit] = []
        for i in range(len(words) - 1):
            if hit_at[i] or hit_at[i + 1]:
                continue
            left, right = words[i], words[i + 1]
            if right.start - left.end > GLUE_MAX_GAP:
                continue
            a, b = basic(left.text), basic(right.text)
            if not a or not b:
                continue
            if min(len(a), len(b)) > GLUE_MAX_PART or len(a) + len(b) < GLUE_MIN_LEN:
                continue
            rule = self.check_variants(variants(a + b))
            if rule:
                out.append(Hit(left.start, right.end, left.text + right.text, rule,
                               min(_prob(left), _prob(right))))
        return out


def _prob(word) -> float:
    return float(getattr(word, "prob", 1.0) or 0.0)


def core_span(hit: Hit, keep_head: float, keep_tail: float, min_mute: float) -> tuple:
    start = hit.start + max(0.0, keep_head)
    end = hit.end - max(0.0, keep_tail)
    if end - start >= min_mute:
        return start, end
    middle = (hit.start + hit.end) / 2.0
    half = min_mute / 2.0
    start, end = max(hit.start, middle - half), min(hit.end, middle + half)
    return (hit.start, hit.end) if end <= start else (start, end)


def hits_to_spans(hits: Iterable[Hit], duration: float, keep_edges: bool,
                  keep_head: float, keep_tail: float, min_mute: float,
                  pad_before: float, pad_after: float, merge_gap: float) -> List[tuple]:
    spans: List[list] = []
    gap = 0.0 if keep_edges else merge_gap
    for hit in hits:
        if keep_edges:
            start, end = core_span(hit, keep_head, keep_tail, min_mute)
        else:
            start, end = hit.start - pad_before, hit.end + pad_after
        start = max(0.0, start)
        end = min(duration or 1e9, end)
        if end <= start:
            continue
        if spans and start - spans[-1][1] <= gap:
            spans[-1][1] = max(spans[-1][1], end)
            spans[-1][2] = "%s, %s" % (spans[-1][2], hit.text)
        else:
            spans.append([start, end, hit.text])
    return [(s, e, label) for s, e, label in spans]


VOWELS = "аеиоуыэюя"
PREFIX_TAIL = VOWELS + "ъь"
SYMBOLS = "#@$%&*!?"


def normalize_word(text: str) -> str:
    text = str(text).lower().replace("ё", "е")
    return re.sub(r"[^\w*]", "", text, flags=re.UNICODE)


def load_stem_rules(path) -> dict:
    import os
    rules = {"deny": [], "allow": set()}
    if not path or not os.path.isfile(path):
        return rules
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            allowed = normalize_word(line[1:]).replace("*", "")
            if allowed:
                rules["allow"].add(allowed)
            continue
        pattern = normalize_word(line)
        if pattern:
            rules["deny"].append(pattern)
    return rules


def stem_is_banned(text: str, rules: dict) -> bool:
    clean = normalize_word(text)
    if not clean:
        return False
    if clean in rules["allow"]:
        return False
    for rule in rules["deny"]:
        head = rule.startswith("*")
        tail = rule.endswith("*")
        core = rule.strip("*")
        if not core:
            continue
        if head and tail:
            if core[0] not in VOWELS:
                if core in clean:
                    return True
                continue
            position = 0
            while True:
                found = clean.find(core, position)
                if found < 0:
                    break
                if found == 0 or clean[found - 1] in PREFIX_TAIL:
                    return True
                position = found + 1
        elif tail:
            if clean.startswith(core):
                return True
        elif head:
            if clean.endswith(core):
                return True
        elif clean == core:
            return True
    return False


def mask_word(text: str) -> str:
    raw = str(text)
    head = 0
    while head < len(raw) and not raw[head].isalnum():
        head += 1
    tail = len(raw)
    while tail > head and not raw[tail - 1].isalnum():
        tail -= 1
    core = raw[head:tail]
    if len(core) < 3:
        hidden = "".join(random.choice(SYMBOLS) for _ in range(max(2, len(core))))
        return raw[:head] + hidden + raw[tail:]
    middle = "".join(random.choice(SYMBOLS) for _ in range(len(core) - 2))
    return raw[:head] + core[0] + middle + core[-1] + raw[tail:]


def apply_stem_censor(words, rules, lead, tail):
    if not rules or not rules["deny"]:
        return 0, []
    spans = []
    hits = 0
    for word in words:
        if not stem_is_banned(word["text"], rules):
            continue
        hits += 1
        word["display"] = mask_word(word["text"])
        span = max(0.0, word["end"] - word["start"])
        head = min(max(0.0, lead), span * 0.25)
        back = min(max(0.0, tail), span * 0.25)
        start = word["start"] + head
        end = word["end"] - back
        if end <= start:
            middle = (word["start"] + word["end"]) / 2.0
            start = middle - 0.025
            end = middle + 0.025
        spans.append((start, end))
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return hits, [(round(a, 3), round(b, 3)) for a, b in merged]
