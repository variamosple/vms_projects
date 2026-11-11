import argparse
import json
from pathlib import Path

from . import validate_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Valida una captura JSON local y emite un informe en consola.")
    parser.add_argument("snapshot", type=Path, help="Archivo JSON con el contrato de captura versión 1.")
    args = parser.parse_args()
    try:
        with args.snapshot.open(encoding="utf-8") as source:
            result = validate_snapshot(json.load(source))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
