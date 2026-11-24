from __future__ import annotations


def parse_time(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    if text.startswith("+"):
        text = text[1:]
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError("Не понял время: %r (нужно 1:23:45 / 23:45 / 3725)" % value)
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        raise ValueError(
            "Не понял время: %r (нужно 1:23:45 / 23:45 / 3725)" % value) from None
    total = 0.0
    for number in numbers:
        total = total * 60.0 + number
    return total


def require_time(value) -> float:
    seconds = parse_time(value)
    if seconds is None:
        raise ValueError("Не понял время: %r (нужно 1:23:45 / 23:45 / 3725)" % value)
    return seconds


def format_time(seconds) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return "%d:%02d:%06.3f" % (hours, minutes, seconds % 60)


def format_hms(seconds) -> str:
    seconds = max(0.0, float(seconds))
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs)
