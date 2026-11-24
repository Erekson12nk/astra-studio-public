"""clipper — обучающийся конвейер нарезки коротких роликов.

Фаза 1: эвристики + языковая модель по расшифровке. Обучения нет.
Всё, что делает система, пишется в SQLite — это будущий обучающий датасет.
"""

from .config import Config, DEFAULT
from .models import Candidate, Sentence, Task, Transcript, Word
from .store import Store

__version__ = "0.1.0"

__all__ = [
    "Config", "DEFAULT", "Store",
    "Task", "Candidate", "Sentence", "Transcript", "Word",
]
