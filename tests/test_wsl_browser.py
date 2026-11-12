"""Tests for the WSL browser shim."""

import os
import subprocess
from pathlib import Path

import pytest

from snowflake_mcp_server.utils import wsl_browser


def _fake_bin(directory: Path, name: str, log: Path) -> None:
    script = directory / name
    script.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
    script.chmod(0o700)


@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _fake_bin(bin_dir, "explorer.exe", log)
    _fake_bin(bin_dir, "wslview", log)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setattr(wsl_browser, "_shim_dir", None)
    monkeypatch.setattr(wsl_browser, "is_wsl", lambda: True)
    return bin_dir, log


def test_shim_opens_urls_with_wslview_and_forwards_the_rest(
    fake_env: tuple[Path, Path],
) -> None:
    _, log = fake_env
    shim_dir = wsl_browser.install_wsl_browser_shim()
    assert shim_dir is not None
    assert os.environ["PATH"].startswith(shim_dir + os.pathsep)

    url = "https://example.com/oauth/authorize?a=1&b=%7C"
    subprocess.run(["explorer.exe", url], check=True)
    subprocess.run(["explorer.exe", "C:\\Users"], check=True)

    assert log.read_text().splitlines() == [
        f"wslview -s {url}",
        "explorer.exe C:\\Users",
    ]


def test_shim_is_idempotent(fake_env: tuple[Path, Path]) -> None:
    first = wsl_browser.install_wsl_browser_shim()
    path_after_first = os.environ["PATH"]
    assert wsl_browser.install_wsl_browser_shim() == first
    assert os.environ["PATH"] == path_after_first


def test_shim_not_installed_outside_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wsl_browser, "_shim_dir", None)
    monkeypatch.setattr(wsl_browser, "is_wsl", lambda: False)
    path = os.environ.get("PATH")
    assert wsl_browser.install_wsl_browser_shim() is None
    assert os.environ.get("PATH") == path
