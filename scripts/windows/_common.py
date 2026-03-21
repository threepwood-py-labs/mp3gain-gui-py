from __future__ import annotations

import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

TEST_DEPENDENCIES = [
    "pytest>=8.0",
    "pytest-cov>=5.0",

    "pytest-qt>=4.4",
    "PySide6>=6.10.2",

]

PACKAGE_DEPENDENCIES = [
    "Nuitka>=4.0.6",
    "zstandard>=0.25.0",
]


def _ensure_declared_dependencies(
    repo_root: Path,
    dependencies: list[str],
    module_names: list[str],
    description: str,
) -> int:
    """Install missing helper dependencies into the local project interpreter."""

    python_exe = get_python(repo_root)
    if not python_exe.exists():
        print(
            "ERROR: local interpreter not found at .venv\\Scripts\\python.exe.",
            file=sys.stderr,
        )
        print("Run: python scripts\\windows\\setup_env.py", file=sys.stderr)
        return 1

    missing_dependencies: list[str] = []
    for dependency, module_name in zip(dependencies, module_names, strict=True):
        has_module = subprocess.run(
            [str(python_exe), "-c", f"import {module_name}"],
            cwd=repo_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        if not has_module:
            missing_dependencies.append(dependency)

    if not missing_dependencies:
        return 0

    uv_exe = shutil.which("uv")
    if uv_exe is None:
        print("ERROR: uv executable not found in PATH.", file=sys.stderr)
        print("Install uv and retry.", file=sys.stderr)
        return 1

    print(f"Installing {description} dependencies into local environment")
    print("with uv pip install")
    cmd = [
        uv_exe,
        "pip",
        "install",
        "--python",
        str(python_exe),
        *missing_dependencies,
    ]
    return subprocess.run(cmd, cwd=repo_root, check=False).returncode


def ensure_venv(repo_root: Path) -> int:
    """Ensure the project-local virtual environment exists."""

    venv_dir = repo_root / ".venv"
    if venv_dir.exists():
        return 0

    setup_script = repo_root / "scripts" / "windows" / "setup_env.py"
    python_exe = shutil.which("python") or shutil.which("py")

    if not setup_script.exists():
        print(
            "ERROR: setup script missing at scripts\\windows\\setup_env.py.",
            file=sys.stderr,
        )
        return 1

    if python_exe is None:
        print("ERROR: python executable not found in PATH.", file=sys.stderr)
        print("Install Python and retry.", file=sys.stderr)
        return 1

    return subprocess.run(
        [python_exe, str(setup_script)],
        cwd=repo_root,
        check=False,
    ).returncode


def ensure_test_dependencies(repo_root: Path) -> int:
    """Ensure pytest tooling is available in the local project interpreter."""

    return _ensure_declared_dependencies(
        repo_root=repo_root,
        dependencies=TEST_DEPENDENCIES,
        module_names=[
            "pytest",
            "pytest_cov",

            "pytestqt",
            "PySide6",

        ],
        description="test",
    )


def ensure_package_dependencies(repo_root: Path) -> int:
    """Ensure Nuitka packaging tools are available in the local project interpreter."""

    return _ensure_declared_dependencies(
        repo_root=repo_root,
        dependencies=PACKAGE_DEPENDENCIES,
        module_names=["nuitka", "zstandard"],
        description="packaging",
    )


def get_python(repo_root: Path) -> Path:
    """Return the local project Python interpreter path."""

    return repo_root / ".venv" / "Scripts" / "python.exe"


def get_pythonw(repo_root: Path) -> Path:
    """Return the local project windowless Python interpreter path."""

    return repo_root / ".venv" / "Scripts" / "pythonw.exe"
