import importlib
import sys

import pytest

from src.multimodel_validation.cli_input import read_local_capture


@pytest.mark.parametrize("entrypoint", [
    "__main__", "constraints", "correspondence", "traceability", "transition", "versions",
])
@pytest.mark.parametrize("absolute", [False, True])
def test_validation_cli_rejects_paths_outside_working_directory(entrypoint, absolute, tmp_path, monkeypatch, capsys):
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    external = tmp_path / "capture.json"
    external.write_text("{}", encoding="utf-8")
    argument = str(external) if absolute else "../capture.json"
    monkeypatch.chdir(working_dir)
    monkeypatch.setattr(sys, "argv", ["validate", argument])
    module = importlib.import_module(f"src.multimodel_validation.{entrypoint}")

    with pytest.raises(SystemExit) as error:
        module.main()

    assert error.value.code == 2
    assert "La captura debe estar dentro del directorio de trabajo." in capsys.readouterr().err


def test_validation_cli_rejects_a_symlink_to_an_external_capture(tmp_path, monkeypatch):
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    external = tmp_path / "capture.json"
    external.write_text("{}", encoding="utf-8")
    link = working_dir / "capture.json"
    try:
        link.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("Este entorno no permite crear enlaces simbólicos.")
    monkeypatch.chdir(working_dir)

    with pytest.raises(ValueError, match="dentro del directorio de trabajo"):
        read_local_capture(link)
