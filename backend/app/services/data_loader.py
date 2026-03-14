import json
from pathlib import Path
from typing import Any


def load_json_file(file_path: Path) -> Any:
    if not file_path.exists():
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)