"""Build a Windows Nuitka package from the project-local interpreter."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from _common import ensure_package_dependencies, ensure_venv, get_python


@dataclass(frozen=True)
class NuitkaConfig:
    """Normalized packaging settings loaded from ``pyproject.toml``."""

    app_package: str
    output_name: str
    mode: str
    windows_console_mode: str
    enable_plugins: tuple[str, ...]
    include_qt_plugins: tuple[str, ...]
    include_data_files: tuple[str, ...]


def _expect_string(value: object, field_name: str) -> str:
    """Return one string field or raise a descriptive configuration error."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"tool.workspace.nuitka.{field_name} must be a non-empty string."
        )
    return value


def _expect_string_list(value: object, field_name: str) -> tuple[str, ...]:
    """Return a tuple of strings or raise a descriptive configuration error."""

    if not isinstance(value, list):
        raise ValueError(
            f"tool.workspace.nuitka.{field_name} must be a list of strings."
        )

    normalized_items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"tool.workspace.nuitka.{field_name} must contain non-empty strings."
            )
        normalized_items.append(item)
    return tuple(normalized_items)


def load_nuitka_config(repo_root: Path) -> NuitkaConfig:
    """Load and validate Nuitka settings from ``pyproject.toml``."""

    pyproject_path = repo_root / "pyproject.toml"
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    tool_section = payload.get("tool")
    if not isinstance(tool_section, dict):
        raise ValueError("pyproject.toml is missing the [tool] table.")

    workspace_section = tool_section.get("workspace")
    if not isinstance(workspace_section, dict):
        raise ValueError("pyproject.toml is missing the [tool.workspace] table.")

    nuitka_section = workspace_section.get("nuitka")
    if not isinstance(nuitka_section, dict):
        raise ValueError("pyproject.toml is missing the [tool.workspace.nuitka] table.")

    config = NuitkaConfig(
        app_package=_expect_string(nuitka_section.get("app_package"), "app_package"),
        output_name=_expect_string(nuitka_section.get("output_name"), "output_name"),
        mode=_expect_string(nuitka_section.get("mode"), "mode"),
        windows_console_mode=_expect_string(
            nuitka_section.get("windows_console_mode"),
            "windows_console_mode",
        ),
        enable_plugins=_expect_string_list(
            nuitka_section.get("enable_plugins"),
            "enable_plugins",
        ),
        include_qt_plugins=_expect_string_list(
            nuitka_section.get("include_qt_plugins"),
            "include_qt_plugins",
        ),
        include_data_files=_expect_string_list(
            nuitka_section.get("include_data_files"),
            "include_data_files",
        ),
    )
    if config.mode not in {"standalone", "onefile"}:
        raise ValueError(
            "tool.workspace.nuitka.mode must be 'standalone' or 'onefile'."
        )
    if config.windows_console_mode not in {"force", "disable", "attach", "hide"}:
        raise ValueError(
            "tool.workspace.nuitka.windows_console_mode must be one of "
            "'force', 'disable', 'attach', or 'hide'."
        )
    return config


def _resolve_data_file_argument(repo_root: Path, raw_value: str) -> str:
    """Resolve one ``--include-data-files`` value relative to the repo root."""

    source_text, separator, target_text = raw_value.partition("=")
    if separator != "=" or not source_text or not target_text:
        raise ValueError(
            "Each include_data_files entry must use the form "
            "'relative/source=relative/target'."
        )

    source_path = (repo_root / source_text).resolve()
    if not source_path.exists():
        raise ValueError(
            f"Configured include_data_files source does not exist: {source_path}"
        )

    return f"{source_path}={target_text}"


def build_command(
    repo_root: Path,
    config: NuitkaConfig,
    extra_args: list[str],
) -> tuple[list[str], dict[str, str]]:
    """Construct the Nuitka command line and environment."""

    python_exe = get_python(repo_root)
    package_root = repo_root / "src" / config.app_package
    build_root = repo_root / "build" / "nuitka"
    output_dir = build_root / config.mode
    report_path = build_root / "reports" / f"{config.output_name}-{config.mode}.xml"

    if not package_root.is_dir():
        raise ValueError(f"Configured app_package directory does not exist: {package_root}")
    if not (package_root / "__main__.py").is_file():
        main_module_path = package_root / "__main__.py"
        raise ValueError(
            f"Configured app_package is missing __main__.py: {main_module_path}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        str(python_exe),
        "-m",
        "nuitka",
        f"--mode={config.mode}",
        "--python-flag=-m",
        "--assume-yes-for-downloads",
        f"--main={package_root}",
        f"--output-dir={output_dir}",
        f"--report={report_path}",
        f"--output-filename={config.output_name}",
        f"--windows-console-mode={config.windows_console_mode}",
        *[f"--enable-plugin={plugin_name}" for plugin_name in config.enable_plugins],
        *[
            f"--include-data-files={_resolve_data_file_argument(repo_root, item)}"
            for item in config.include_data_files
        ],
        *extra_args,
    ]

    if config.include_qt_plugins:
        command.append(
            f"--include-qt-plugins={','.join(config.include_qt_plugins)}"
        )

    environment = os.environ.copy()
    src_path = repo_root / "src"
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        str(src_path)
        if not existing_pythonpath
        else os.pathsep.join([str(src_path), existing_pythonpath])
    )
    return command, environment


def main() -> int:
    """Build the configured standalone artifact with Nuitka."""

    repo_root = Path(__file__).resolve().parents[2]
    rc = ensure_venv(repo_root)
    if rc != 0:
        return rc

    rc = ensure_package_dependencies(repo_root)
    if rc != 0:
        return rc

    python_exe = get_python(repo_root)
    if not python_exe.exists():
        print(
            "ERROR: local interpreter not found at .venv\\Scripts\\python.exe.",
            file=sys.stderr,
        )
        print("Run: python scripts\\windows\\setup_env.py", file=sys.stderr)
        return 1

    try:
        config = load_nuitka_config(repo_root)
        command, environment = build_command(repo_root, config, sys.argv[1:])
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"ERROR: failed to prepare Nuitka build: {exc}", file=sys.stderr)
        return 1

    print("Running Nuitka build:")
    print(" ".join(command))
    return subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        env=environment,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
