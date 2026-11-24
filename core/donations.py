from __future__ import annotations

import json
import os
import re
import time
import wave
from pathlib import Path

import numpy as np

from . import jingle as _jingle
from . import voiceid as _voice

RATE = 16000
SOUND_THRESHOLD = 0.6
READ_GAP = 3.0
READ_BRIDGE = 1.5
READ_MAX = 40.0
REPLY_MAX = 45.0
LEAD = 4.0
EDGE = 0.2
MOSTLY = 0.6
ROBOT_LOOK = 10.0
SOUNDS_KEY = "donation_sounds"
VOICE_KEY = "voice"
ROBOT_KEY = "robot"
VOICE_FILE = "voice.npy"
ROBOT_FILE = "robot.npy"
SOUND_STEM = "donation_"

KEEP = "оставить"
CUT = "вырезать"
UNSURE = "не ясно"

STEM = re.compile(r"[а-яёa-z0-9]{4,}", re.IGNORECASE)


def _folder(profile) -> Path:
    return Path(profile["folder"])


def sounds(profile) -> list:
    out = []
    for entry in profile.get(SOUNDS_KEY) or ():
        path = _folder(profile) / str(entry.get("file") or "")
        if path.is_file():
            out.append(dict(entry, path=str(path)))
    return out


def write_wav(path, samples, rate=RATE) -> str:
    samples = np.asarray(samples)
    if samples.dtype != np.int16:
        samples = np.clip(np.asarray(samples, dtype=np.float32) * 32767.0,
                          -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(rate)
        sink.writeframes(samples.tobytes())
    return str(path)


def read_wav(path):
    with wave.open(str(path), "rb") as source:
        data = source.readframes(source.getnframes())
    return np.frombuffer(data, dtype=np.int16)


def add_sound(profile, samples, label="", source="", start=0.0, end=0.0) -> dict:
    place = _folder(profile)
    place.mkdir(parents=True, exist_ok=True)
    taken = {str(entry.get("file")) for entry in profile.get(SOUNDS_KEY) or ()}
    number = 1
    while "%s%d.wav" % (SOUND_STEM, number) in taken or (
            place / ("%s%d.wav" % (SOUND_STEM, number))).exists():
        number += 1
    name = "%s%d.wav" % (SOUND_STEM, number)
    write_wav(place / name, samples)
    entry = {"file": name, "label": str(label or "").strip(),
             "length": round(len(samples) / float(RATE), 3),
             "source": os.path.basename(str(source)), "start": round(float(start), 3),
             "end": round(float(end), 3), "added": time.time()}
    profile[SOUNDS_KEY] = list(profile.get(SOUNDS_KEY) or ()) + [entry]
    return entry


def remove_sound(profile, name) -> None:
    kept = []
    for entry in profile.get(SOUNDS_KEY) or ():
        if entry.get("file") == name:
            try:
                (_folder(profile) / name).unlink()
            except OSError:
                pass
            continue
        kept.append(entry)
    profile[SOUNDS_KEY] = kept


def voice_ref(profile, key=VOICE_KEY):
    name = profile.get(key)
    if not name:
        return None
    return _voice.load_ref(_folder(profile) / name)


def set_voice_ref(profile, vector, key=VOICE_KEY) -> str:
    name = VOICE_FILE if key == VOICE_KEY else ROBOT_FILE
    _voice.save_ref(_folder(profile) / name, vector)
    profile[key] = name
    return name


def piece(audio, start, end):
    first = max(0, int(float(start) * RATE))
    last = min(len(audio), int(float(end) * RATE))
    return np.asarray(audio[first:last], dtype=np.int16)


def find_events(spectrum, entries, threshold=SOUND_THRESHOLD):
    found = []
    for entry in entries:
        try:
            samples = read_wav(entry["path"])
            hits = _jingle.find_sample(spectrum, samples, threshold=threshold)
        except (OSError, ValueError, wave.Error):
            continue
        for hit in hits:
            found.append(dict(hit, label=entry.get("label") or "",
                              sound=entry.get("file") or ""))
    found.sort(key=lambda item: item["score"], reverse=True)
    kept = []
    for item in found:
        if any(item["start"] < other["end"] and other["start"] < item["end"]
               for other in kept):
            continue
        kept.append(item)
    kept.sort(key=lambda item: item["start"])
    return kept


def robot_from_event(voice, event, streamer=None, look=ROBOT_LOOK, same=_voice.SAME):
    times = np.asarray(voice["time"], dtype=np.float32)
    inside = ((times >= float(event["end"])) & (times <= float(event["end"]) + look)
              & np.asarray(voice["voiced"], dtype=bool))
    if streamer is not None:
        score = np.nan_to_num(_voice.similarity(voice, streamer), nan=1.0)
        inside &= score < same
    picked = np.flatnonzero(inside)
    if picked.size < 2:
        return None
    vectors = np.asarray(voice["vec"], dtype=np.float32)[picked]
    return _voice.unit(vectors.mean(axis=0))


def reading_end(event, marks, voice, gap=READ_GAP, bridge=READ_BRIDGE, longest=READ_MAX):
    if marks is None or voice is None:
        return float(event["end"])
    times = np.asarray(voice["time"], dtype=np.float32)
    half = float(voice["step"]) / 2.0
    robot = np.asarray([mark == _voice.ROBOT for mark in marks], dtype=bool)
    begin = float(event["end"])
    started = np.flatnonzero(robot & (times >= begin - half) & (times <= begin + gap))
    if started.size == 0:
        return begin
    index = int(started[0])
    finish = float(times[index]) + half
    limit = begin + longest
    while index + 1 < times.size and float(times[index + 1]) <= limit:
        index += 1
        if robot[index]:
            finish = float(times[index]) + half
        elif float(times[index]) - finish > bridge:
            break
    return min(finish, limit)


def guess_reading_end(event, lines, gap=READ_GAP, longest=READ_MAX):
    begin = float(event["end"])
    finish = begin
    for left, right, _text in lines or ():
        if left < begin - 0.3:
            continue
        if left > begin + gap:
            break
        finish = max(finish, min(right, begin + longest))
    return finish


def discover(voice, marks, events, lead=LEAD, gap=READ_GAP, least=1.5, limit=8):
    if marks is None:
        return []
    robot = _voice.spans(voice, marks, _voice.ROBOT, min_len=least, bridge=READ_BRIDGE)
    found = []
    for start, end in robot:
        covered = any(event["start"] - 1.0 <= start <= event["end"] + gap
                      for event in events)
        if covered:
            continue
        if found and start - found[-1]["robot_start"] < lead * 2:
            continue
        found.append({"start": max(0.0, start - lead), "end": start,
                      "robot_start": start, "robot_end": end})
        if len(found) >= limit:
            break
    return found


def segments_of(transcript):
    if transcript is None:
        return []
    raw = transcript.segments if hasattr(transcript, "segments") else transcript
    out = []
    for item in raw:
        if isinstance(item, dict):
            out.append((float(item["start"]), float(item["end"]),
                        str(item.get("text") or "").strip()))
        else:
            out.append((float(item.start), float(item.end), str(item.text or "").strip()))
    out.sort()
    return out


def load_transcript(source):
    path = str(source) + ".speech.json"
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return segments_of(data.get("segments") or [])


def text_between(lines, start, end):
    words = []
    for left, right, text in lines:
        if right <= start or left >= end:
            continue
        words.append(text)
    return " ".join(part for part in words if part).strip()


def lines_after(lines, start, longest=REPLY_MAX, count=8):
    out = []
    for left, right, text in lines:
        if left < start - 0.3:
            continue
        if left > start + longest or len(out) >= count:
            break
        out.append((left, right, text))
    return out


JUDGE_SYSTEM = """Ты — редактор коротких роликов из стримов. В клип попал донат:
зритель прислал деньги с сообщением, его зачитал робот, потом стример мог ответить.

Реши, связан ли донат с темой клипа. Связан — если сообщение доната или ответ
стримера продолжают то, о чём идёт речь в клипе, или донат и есть смешной момент
клипа. Не связан — если донат про другое: приветы, просьбы включить трек,
реклама, вопросы не по теме, просто сумма без текста.

Отдельно укажи, сколько первых реплик стримера после доната — это ответ на донат
(0, если стример сразу вернулся к теме).

Отвечай на русском языке."""

JUDGE_TEMPLATE = """О чём клип (без доната):
{topic}

Донат{label}:
{donation}

Реплики стримера сразу после доната, по номерам:
{reply}

Ответ — только JSON: {{"related": true или false, "reply": число, "reason": "коротко почему"}}"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "related": {"type": "boolean"},
        "reply": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["related", "reply", "reason"],
    "additionalProperties": False,
}


def stems(text):
    return {word.lower()[:5] for word in STEM.findall(text or "")}


def judge_by_words(topic, donation, reply_lines):
    if not (donation or "").strip():
        return {"related": False, "reply": len(reply_lines) and 1,
                "reason": "донат без текста"}
    shared = stems(topic) & stems(donation)
    if len(shared) >= 2:
        return {"related": True, "reply": 0,
                "reason": "общие слова с темой: %s" % ", ".join(sorted(shared)[:4])}
    return {"related": None, "reply": 0, "reason": "по словам не понять"}


def judge_with(backend, cfg):
    complete = getattr(backend, "complete", None)
    if not callable(complete):
        return judge_by_words
    from . import detect as _detect

    def ask(topic, donation, reply_lines, label=""):
        if not (donation or "").strip():
            return judge_by_words(topic, donation, reply_lines)
        reply = "\n".join("[%d] %s" % (number, text)
                          for number, (_l, _r, text) in enumerate(reply_lines, 1))
        user = JUDGE_TEMPLATE.format(topic=(topic or "(пусто)")[:1800],
                                     donation=donation[:600],
                                     label=(" (звук «%s»)" % label) if label else "",
                                     reply=reply or "(нет)")
        try:
            answer = _detect.extract_json(complete(JUDGE_SYSTEM, user, cfg, JUDGE_SCHEMA))
        except Exception as error:
            fallback = judge_by_words(topic, donation, reply_lines)
            fallback["reason"] += "; модель не ответила: %s" % str(error).splitlines()[0]
            return fallback
        if not isinstance(answer, dict) or "related" not in answer:
            return judge_by_words(topic, donation, reply_lines)
        try:
            count = int(answer.get("reply") or 0)
        except (TypeError, ValueError):
            count = 0
        return {"related": bool(answer.get("related")),
                "reply": max(0, min(count, len(reply_lines))),
                "reason": str(answer.get("reason") or "").strip()}

    return ask


def plan_clip(start, end, events, lines, marks=None, voice=None, judge=None):
    judge = judge or judge_by_words
    inside = [event for event in events if event["start"] < end and event["end"] > start]
    decisions = []
    for event in inside:
        if marks is None:
            read_end = guess_reading_end(event, lines)
        else:
            read_end = reading_end(event, marks, voice)
        donation = text_between(lines, event["end"] - 0.3, max(read_end, event["end"]) + 0.3)
        if read_end <= event["end"] + 0.05:
            donation = ""
        if not lines:
            decisions.append({"start": float(event["start"]), "end": float(event["end"]),
                              "label": event.get("label") or "",
                              "score": event.get("score"), "read_end": read_end,
                              "text": "", "reason": "нет расшифровки",
                              "action": UNSURE, "cut": None})
            continue
        reply_lines = lines_after(lines, read_end)
        topic = " ".join(text for left, right, text in lines
                         if left < end and right > start
                         and not (left < read_end + REPLY_MAX and right > event["start"]))
        try:
            verdict = judge(topic, donation, reply_lines, event.get("label") or "")
        except TypeError:
            verdict = judge(topic, donation, reply_lines)
        related = verdict.get("related")
        decision = {"start": float(event["start"]), "end": float(event["end"]),
                    "label": event.get("label") or "", "score": event.get("score"),
                    "read_end": read_end, "text": donation, "reason": verdict.get("reason", ""),
                    "action": KEEP if related else (UNSURE if related is None else CUT),
                    "cut": None}
        if related is False:
            finish = read_end
            count = int(verdict.get("reply") or 0)
            if count and reply_lines:
                finish = max(finish, reply_lines[min(count, len(reply_lines)) - 1][1])
            left = max(start, float(event["start"]) - EDGE)
            right = min(end, finish + EDGE)
            if right - left > 0.3:
                decision["cut"] = (left, right, "донат не по теме")
        decisions.append(decision)
    removed = sum(item["cut"][1] - item["cut"][0] for item in decisions if item["cut"])
    return {"decisions": decisions,
            "drops": [item["cut"] for item in decisions if item["cut"]],
            "mostly": (end - start) > 0 and removed / (end - start) >= MOSTLY}


def describe(decision) -> str:
    from . import timecode as _tc

    head = "%s %s" % (_tc.format_hms(decision["start"]), decision["action"])
    if decision.get("label"):
        head += " [%s]" % decision["label"]
    text = (decision.get("text") or "").strip()
    if text:
        head += " — «%s»" % (text[:70] + ("…" if len(text) > 70 else ""))
    if decision.get("reason"):
        head += " (%s)" % decision["reason"]
    return head
