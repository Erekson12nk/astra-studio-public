from __future__ import annotations

import numpy as np

from . import donations as _don
from . import jingle as _jingle
from . import voiceid as _voice

CHECK_DONATIONS = "check_donations"
CHECK_AWAY = "check_away"
TOPIC_REACH = 120.0
AWAY_MOSTLY = 0.5


class Context:
    def __init__(self, source, profile, log=None, progress=None, cancel=None):
        self.source = str(source)
        self.profile = profile
        self.log = log or (lambda text: None)
        self.progress = progress or (lambda part, note="": None)
        self.cancel = cancel or (lambda: False)
        self._audio = None
        self._spectrum = None
        self._voice = None
        self._marks = None
        self._lines = None
        self._embedder = None

    def audio(self):
        if self._audio is None:
            self.log("снимаю звук стрима (один раз, потом из кеша)")
            self._audio = _voice.raw_audio(
                self.source, progress=lambda done, total: self.progress(
                    min(0.99, done / total) if total else 0.0, "звук"))
        return self._audio

    def spectrum(self):
        if self._spectrum is None:
            self.audio()
            self._spectrum = _jingle.spectrum(self.source, samples=_raw_path(self))
        return self._spectrum

    def embedder(self):
        if self._embedder is None:
            self._embedder = _voice.Embedder(report=self.log)
        return self._embedder

    def voice(self):
        if self._voice is None:
            if not _voice.available():
                self.log("нет sherpa-onnx: голоса не различаются, работаю только по звуку")
                return None
            self.log("слушаю голоса по всему стриму (один раз, потом из кеша)")
            self._voice = _voice.track(
                self.source, self.embedder(), audio=None, samples=_raw_path(self),
                progress=lambda index, count: self.progress(
                    index / float(count) if count else 0.0, "голоса"))
        return self._voice

    def marks(self):
        if self._marks is None:
            voice = self.voice()
            if voice is None:
                return None
            refs = {_voice.STREAMER: _don.voice_ref(self.profile, _don.VOICE_KEY),
                    _voice.ROBOT: _don.voice_ref(self.profile, _don.ROBOT_KEY)}
            if all(ref is None for ref in refs.values()):
                return None
            self._marks = _voice.labels(voice, refs)
        return self._marks

    def lines(self):
        if self._lines is None:
            self._lines = _don.load_transcript(self.source)
            if not self._lines:
                self.log("расшифровки стрима нет — текст донатов не прочитать, "
                         "сомнительные донаты останутся (нажмите «Расшифровать»)")
        return self._lines


def _raw_path(context):
    audio = context.audio()
    return getattr(audio, "filename", None)


def build_voice(context):
    voice = context.voice()
    if voice is None:
        raise RuntimeError("нет sherpa-onnx — голос не собрать:  py -m pip install sherpa-onnx")
    ref = _voice.reference(voice)
    if ref is None:
        raise RuntimeError("в стриме слишком мало речи, чтобы собрать голос")
    share = float(np.nanmean(np.nan_to_num(_voice.similarity(voice, ref), nan=-1.0)
                             [np.asarray(voice["voiced"], dtype=bool)] >= _voice.SAME))
    return ref, share


def find_donations(context, threshold=_don.SOUND_THRESHOLD):
    entries = _don.sounds(context.profile)
    if not entries:
        return []
    return _don.find_events(context.spectrum(), entries, threshold=threshold)


def learn_robot(context, events):
    if _don.voice_ref(context.profile, _don.ROBOT_KEY) is not None or not events:
        return None
    voice = context.voice()
    if voice is None:
        return None
    streamer = _don.voice_ref(context.profile, _don.VOICE_KEY)
    for event in events:
        ref = _don.robot_from_event(voice, event, streamer=streamer)
        if ref is not None:
            context._marks = None
            return ref
    return None


def suggest_sounds(context, events):
    marks = context.marks()
    voice = context.voice()
    if marks is None or voice is None:
        return []
    return _don.discover(voice, marks, events)


class Faces:
    def __init__(self):
        try:
            import auto_vertical_editor as engine
        except ImportError:
            from astra import auto_vertical_editor as engine
        self.backend = engine.FaceBackend(None)
        self.name = "faces-%s" % self.backend.mode

    def __call__(self, image):
        return self.backend.detect(image)


def face_detector(log=None):
    try:
        return Faces()
    except Exception as error:
        if log:
            log("детектор лиц движка недоступен (%s), беру запасной"
                % str(error).splitlines()[0])
        return None


def find_away(context):
    from . import away as _away
    from . import profiles as _pro

    photo = _pro.face_path(context.profile)
    voice = context.voice()
    ref = _don.voice_ref(context.profile, _don.VOICE_KEY)
    context.log("ищу, где стример отходил (кадр раз в %.0f с)" % _away.STEP)
    return _away.scan_away(context.source, photo=str(photo) if photo else None,
                           detector=face_detector(context.log),
                           voice=voice, ref=ref, lines=context.lines(),
                           samples=_raw_path(context),
                           progress=lambda done, total: context.progress(
                               done / float(total) if total else 0.0, "кадры"))


def check_moments(context, moments, judge=None, donations=True, away=True):
    events = find_donations(context) if donations else []
    if events:
        context.log("донатов по звуку в стриме: %d" % len(events))
        robot = learn_robot(context, events)
        if robot is not None:
            _don.set_voice_ref(context.profile, robot, _don.ROBOT_KEY)
            context.log("голос робота запомнен по первому донату")
    gone = []
    if away:
        try:
            gone = find_away(context)
        except Exception as error:
            context.log("поиск «отошёл» не удался: %s" % str(error).splitlines()[0])
        for start, end, why in gone:
            context.log("отходил %s–%s (%s)" % (_hms(start), _hms(end), why))
    lines = context.lines() if events else []
    marks = context.marks() if events else None
    voice = context.voice() if marks is not None else None
    out = []
    report = []
    for item in moments:
        if context.cancel():
            break
        start, end = float(item["start"]), float(item["end"])
        fresh = dict(item)
        drops = [tuple(drop) for drop in item.get("drops") or ()]
        length = max(1e-6, end - start)
        lost = sum(max(0.0, min(end, b) - max(start, a)) for a, b, _w in gone)
        if lost / length >= AWAY_MOSTLY:
            report.append("%s убран: стример отходил" % _hms(start))
            continue
        for a, b, why in gone:
            if b > start and a < end:
                drops.append((max(start, a), min(end, b), "отошёл: %s" % why))
        if events:
            plan = _don.plan_clip(start, end, events, _near(lines, start, end), marks=marks,
                                  voice=voice, judge=judge)
            for decision in plan["decisions"]:
                report.append(_don.describe(decision))
            if plan["mostly"]:
                report.append("%s убран: почти весь — донат не по теме" % _hms(start))
                continue
            drops.extend(plan["drops"])
        if drops:
            fresh["drops"] = sorted(drops)
        out.append(fresh)
    return out, report, events, gone


def _near(lines, start, end):
    return [line for line in lines
            if line[1] > start - TOPIC_REACH and line[0] < end + _don.REPLY_MAX]


def _hms(seconds):
    from . import timecode as _tc

    return _tc.format_hms(seconds)
