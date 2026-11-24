# -*- coding: utf-8 -*-
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

from media import build as _build, CACHE as _CACHE

_MEDIA = _build()
_CACHE_U = _CACHE.replace("\\", "/")

import json, os, random, sys

ROOT = sys.argv[1]
out = {}
_real = sys.stdout
sys.stdout = sys.stderr


def rec(key, fn):
    try:
        out[key] = {"ok": True, "value": fn()}
    except Exception as e:
        out[key] = {"ok": False, "err": type(e).__name__, "msg": str(e)[:200]}


class W:
    def __init__(self, start, end, text, prob=1.0):
        self.start, self.end, self.text, self.prob = start, end, text, prob


# слова: обычные, с маскировкой латиницей, растянутые, разорванные надвое
PHRASE = [
    ("привет", 0.0, 0.4), ("всем", 0.4, 0.8), ("сука", 0.9, 1.3),
    ("cyka", 1.4, 1.8), ("сууука", 1.9, 2.4), ("мандарин", 2.5, 3.1),
    ("манда", 3.2, 3.6), ("негромко", 3.7, 4.3), ("негр", 4.4, 4.8),
    ("бл", 5.0, 5.15), ("ять", 5.20, 5.5), ("хорошо", 5.6, 6.0),
    ("блядь", 6.1, 6.5), ("забить", 6.6, 7.0), ("хер", 7.1, 7.4),
    ("похер", 7.5, 7.9), ("bitch", 8.0, 8.4), ("б1ять", 8.5, 8.9),
]
WORDS = [W(s, e, t, 0.9) for t, s, e in PHRASE]

# ---------------- audioclean ----------------
sys.path.insert(0, os.path.join(ROOT, "audioclean"))
from audioclean.config import Settings
from audioclean import profanity as P
from audioclean import textnorm as T

rec("textnorm.basic", lambda: [T.basic(x) for x in
                               ["Привет!", "Ё-моё", "b1tch", "  ", "СУ.КА"]])
rec("textnorm.to_cyr", lambda: [T.to_cyr(x) for x in ["cyka", "b1ath", "xyu"]])
rec("textnorm.collapse", lambda: [T.collapse(x) for x in ["сууукаа", "aaa", "ab"]])
rec("textnorm.variants", lambda: {x: sorted(T.variants(x))
                                  for x in ["сука", "cyka", "сууука", ""]})
rec("textnorm.is_latin", lambda: [T.is_latin(x) for x in ["abc", "абв", "a1", ""]])

cfg = Settings(use_ru=True, use_en=True, use_platform=True,
               keep_edges=True, keep_head=0.07, keep_tail=0.07, min_mute=0.10,
               pad_before=0.12, pad_after=0.15, merge_gap=0.25)
m = P.Matcher(cfg)
rec("matcher.rules_count", lambda: [len(m.rules), len(m.singles),
                                    len(m.phrases), len(m.whitelist)])
rec("matcher.check", lambda: {w: m.check(w) for w in
                              ["сука", "мандарин", "манда", "негромко", "негр",
                               "привет", "cyka", "сууука", "b1ять", "bitch",
                               "похер", "хер", "забить"]})
hits = m.scan(WORDS)
rec("matcher.scan", lambda: [[round(h.start, 3), round(h.end, 3), h.text,
                              h.rule, round(h.prob, 3)] for h in hits])
rec("spans.keep_edges", lambda: [[round(a, 4), round(b, 4), lbl]
                                 for a, b, lbl in P.hits_to_spans(hits, cfg, 10.0)])
cfg2 = Settings(keep_edges=False, pad_before=0.12, pad_after=0.15, merge_gap=0.25)
m2 = P.Matcher(cfg2)
h2 = m2.scan(WORDS)
rec("spans.padded", lambda: [[round(a, 4), round(b, 4), lbl]
                             for a, b, lbl in P.hits_to_spans(h2, cfg2, 10.0)])
rec("core_span", lambda: [round(v, 4) for v in
                          P.core_span(P.Hit(1.0, 1.12, "x", "r"), cfg)])

# ---------------- astra ----------------
sys.path.insert(0, os.path.join(ROOT, "astra"))
import auto_vertical_editor as AVE

rules = AVE.load_censor_rules(os.path.join(ROOT, "astra", "censor.txt"))
rec("astra.rules", lambda: [len(rules["deny"]), len(sorted(rules["allow"]))])
rec("astra.normalize", lambda: [AVE.normalize_word(x) for x in
                                ["Привет!", "Ё-моё", "*корень*", "a-b_c"]])
rec("astra.banned", lambda: {w: AVE.word_is_banned(w, rules) for w in
                             ["сука", "мандарин", "манда", "негромко", "негр",
                              "привет", "блядь", "бля", "ёб", "долбоёб",
                              "хер", "херня", "похер", "убил"]})
random.seed(1234)
rec("astra.mask", lambda: [AVE.mask_word(x) for x in
                           ["блядь", "ab", "a", "«сука»", "х"]])
words = [{"text": t, "start": s, "end": e} for t, s, e in PHRASE]
random.seed(99)
rec("astra.apply_censor", lambda: AVE.apply_stem_test(words, rules)
    if hasattr(AVE, "apply_stem_test") else AVE.apply_censor(words, rules, 0.07, 0.07))
rec("astra.displays", lambda: [w.get("display") for w in words if "display" in w])
rec("astra.mute_filter", lambda: AVE.build_mute_filter([(1.0, 2.0), (3.5, 3.75)]))
rec("astra.constants", lambda: [AVE.CENSOR_SYMBOLS, AVE.VOWELS, AVE.PREFIX_TAIL])

_real.write(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True, default=str))
