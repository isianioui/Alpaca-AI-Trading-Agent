"""
Downloads and installs Alpaca's official CLI (github.com/alpacahq/cli) into
./bin/ for the current OS/arch, without requiring a Go toolchain.

Usage:
    python scripts/install_alpaca_cli.py

Verifies the downloaded archive against the release's published
checksums.txt before extracting. Safe to re-run (overwrites bin/alpaca[.exe]).
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO = "alpacahq/cli"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = PROJECT_ROOT / "bin"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "alpaca-cli-installer"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _detect_asset_name(tag: str) -> tuple[str, str]:
    """Returns (asset_filename, kind) where kind is 'zip' or 'tar.gz'."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        os_name = "windows"
        ext = "zip"
    elif system == "darwin":
        os_name = "darwin"
        ext = "tar.gz"
    elif system == "linux":
        os_name = "linux"
        ext = "tar.gz"
    else:
        raise RuntimeError(f"Unsupported OS: {system}")

    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")

    version = tag.lstrip("v")
    name = f"cli_{version}_{os_name}_{arch}.{ext}"
    return name, ext


def main() -> None:
    print("Fetching latest alpacahq/cli release metadata...")
    release = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
    tag = release["tag_name"]
    print(f"Latest release: {tag}")

    asset_name, kind = _detect_asset_name(tag)
    asset = next((a for a in release["assets"] if a["name"] == asset_name), None)
    if asset is None:
        available = ", ".join(a["name"] for a in release["assets"])
        raise RuntimeError(
            f"No release asset named {asset_name!r} found for {tag}. "
            f"Available assets: {available}. "
            f"Check https://github.com/{REPO}/releases for a manual download, "
            f"or install the Go toolchain and run: "
            f"go install github.com/alpacahq/cli/cmd/alpaca@latest"
        )

    checksums_asset = next((a for a in release["assets"] if a["name"] == "checksums.txt"), None)
    expected_sha256 = None
    if checksums_asset:
        checksums_text = _get(checksums_asset["browser_download_url"]).decode()
        for line in checksums_text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == asset_name:
                expected_sha256 = parts[0]
                break

    print(f"Downloading {asset_name}...")
    archive_bytes = _get(asset["browser_download_url"])

    if expected_sha256:
        actual_sha256 = hashlib.sha256(archive_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"Checksum mismatch for {asset_name}: expected {expected_sha256}, got {actual_sha256}"
            )
        print("Checksum verified.")
    else:
        print("WARNING: could not find a published checksum for this asset; skipping verification.")

    BIN_DIR.mkdir(exist_ok=True)
    binary_name = "alpaca.exe" if kind == "zip" else "alpaca"
    dest = BIN_DIR / binary_name

    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            with zf.open("alpaca.exe") as src, open(dest, "wb") as out:
                out.write(src.read())
    else:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            member = tf.getmember("alpaca")
            extracted = tf.extractfile(member)
            with open(dest, "wb") as out:
                out.write(extracted.read())
        dest.chmod(0o755)

    print(f"Installed Alpaca CLI {tag} -> {dest}")
    print("Verify with: python main.py cli-check")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - CLI entrypoint, report and exit non-zero
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
