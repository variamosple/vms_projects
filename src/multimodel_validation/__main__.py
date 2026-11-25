import argparse
import json
from pathlib import Path

from . import validate_snapshot
from .cli_input import read_local_capture


def main() -> None:
    parser = argparse.ArgumentParser(description="Valida una captura JSON local y emite un informe en consola.")
    parser.add_argument("snapshot", type=Path, help="Archivo JSON con el contrato de captura versión 1, dentro del directorio de trabajo.")
    args = parser.parse_args()
    try:
        result = validate_snapshot(read_local_capture(args.snapshot))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
