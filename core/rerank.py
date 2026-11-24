from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

FEATURES = ["model_score", "duration", "n_sentences", "chars",
            "words_per_sec"]
FILE_NAME = "rerank.json"
HOLDOUT = 0.25
MIN_PAIRS = 12


def engine_fill(rows):
    scores = sorted(float(row["model_score"]) for row in rows
                    if not row.get("hand") and row.get("model_score") is not None)
    if not scores:
        return None
    half = len(scores) // 2
    return scores[half] if len(scores) % 2 else (scores[half - 1] + scores[half]) / 2.0


def feature_vector(row, fill=None) -> list[float]:
    raw = row.get("features") or {}
    if row.get("hand") and fill is not None:
        values = [float(fill)]
    else:
        values = [float(row.get("model_score") or 0.0)]
    for name in FEATURES[1:]:
        try:
            values.append(float(raw.get(name, 0.0)))
        except (TypeError, ValueError):
            values.append(0.0)
    return values


def pairs_from_ratings(ratings) -> list[tuple[int, int]]:
    graded = [(cid, value) for cid, value in ratings.items() if value > 0]
    out = []
    for i, (left, left_value) in enumerate(graded):
        for right, right_value in graded[i + 1:]:
            if left_value > right_value:
                out.append((left, right))
            elif right_value > left_value:
                out.append((right, left))
    return out


def standardise(rows):
    columns = list(zip(*rows)) if rows else []
    means = [sum(c) / len(c) for c in columns]
    spreads = []
    for column, mean in zip(columns, means):
        variance = sum((v - mean) ** 2 for v in column) / max(1, len(column) - 1)
        spreads.append(math.sqrt(variance) or 1.0)
    return means, spreads


def apply_scaling(vector, means, spreads):
    return [(v - m) / s for v, m, s in zip(vector, means, spreads)]


def fit(pairs, vectors, steps=4000, rate=0.2, decay=0.01):
    size = len(FEATURES)
    weights = [0.0] * size
    if not pairs:
        return weights
    for _ in range(steps):
        gradient = [0.0] * size
        for win, lose in pairs:
            diff = [a - b for a, b in zip(vectors[win], vectors[lose])]
            margin = sum(w * d for w, d in zip(weights, diff))
            pull = 1.0 / (1.0 + math.exp(min(60.0, max(-60.0, margin))))
            for i, d in enumerate(diff):
                gradient[i] += -pull * d
        scale = rate / len(pairs)
        for i in range(size):
            weights[i] -= scale * gradient[i] + rate * decay * weights[i]
    return weights


def accuracy(pairs, vectors, weights) -> float:
    if not pairs:
        return 0.0
    hits = 0.0
    for win, lose in pairs:
        margin = sum(w * (a - b) for w, a, b in
                     zip(weights, vectors[win], vectors[lose]))
        hits += 1.0 if margin > 0 else (0.5 if margin == 0 else 0.0)
    return hits / float(len(pairs))


def baseline_accuracy(pairs, raw) -> float:
    if not pairs:
        return 0.0
    hits = 0.0
    for win, lose in pairs:
        if raw[win][0] > raw[lose][0]:
            hits += 1.0
        elif raw[win][0] == raw[lose][0]:
            hits += 0.5
    return hits / float(len(pairs))


def train(rows, ratings, comparisons, seed=17):
    fill = engine_fill(rows)
    raw = {row["id"]: feature_vector(row, fill) for row in rows}
    pairs = [(w, l) for w, l in comparisons if w in raw and l in raw]
    pairs += [(w, l) for w, l in pairs_from_ratings(ratings) if w in raw and l in raw]
    pairs = list({(w, l) for w, l in pairs if w != l})
    report = {"pairs": len(pairs), "candidates": len(raw), "trained": False}
    if len(pairs) < MIN_PAIRS:
        report["reason"] = ("сравнений слишком мало: %d, нужно хотя бы %d"
                            % (len(pairs), MIN_PAIRS))
        return None, report

    random.Random(seed).shuffle(pairs)
    cut = max(1, int(len(pairs) * HOLDOUT))
    holdout, learning = pairs[:cut], pairs[cut:]
    if not learning:
        report["reason"] = "после разбиения не осталось пар для обучения"
        return None, report

    means, spreads = standardise([raw[cid] for cid in raw])
    scaled = {cid: apply_scaling(vector, means, spreads) for cid, vector in raw.items()}
    weights = fit(learning, scaled)

    model = {
        "features": FEATURES,
        "means": means,
        "spreads": spreads,
        "weights": weights,
        "fill": fill,
    }
    report.update({
        "trained": True,
        "learning_pairs": len(learning),
        "holdout_pairs": len(holdout),
        "train_accuracy": accuracy(learning, scaled, weights),
        "holdout_accuracy": accuracy(holdout, scaled, weights),
        "baseline_accuracy": baseline_accuracy(holdout, raw),
    })
    report["better_than_engine"] = (
        report["holdout_accuracy"] > report["baseline_accuracy"])
    report["shaky"] = len(holdout) < 40
    model["report"] = dict(report)
    model["trained_at"] = time.time()
    return model, report


def score(model, row) -> float:
    vector = apply_scaling(feature_vector(row, model.get("fill")),
                           model["means"], model["spreads"])
    return sum(w * v for w, v in zip(model["weights"], vector))


def save(model, path) -> Path:
    path = Path(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load(path):
    path = Path(str(path))
    if not path.is_file():
        return None
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(model, dict) or "weights" not in model:
        return None
    return model
