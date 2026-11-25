import json
from pathlib import Path
from typing import Any


def read_local_capture(path: Path) -> Any:
    """Read a CLI capture from the working directory or one of its subdirectories."""
    root = Path.cwd().resolve()
    capture = path.resolve()
    if not capture.is_relative_to(root):
        raise ValueError("La captura debe estar dentro del directorio de trabajo.")
    with capture.open(encoding="utf-8") as source:
        return json.load(source)
