"""Create the offline setup payload from the onedir PyInstaller output."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def create_payload(source, destination):
    source, destination = Path(source), Path(destination)
    if not (source / "PawapoDiff.exe").is_file():
        raise ValueError("Build the Windows application before creating its installer.")
    destination.mkdir(parents=True, exist_ok=True)
    payload = destination / "payload.zip"
    with ZipFile(payload, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ValueError("Symlinks are not permitted in the Windows payload.")
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    digest = hashlib.sha256()
    with payload.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    (destination / "payload.json").write_text(
        json.dumps({"sha256": digest.hexdigest()}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    create_payload(args.source, args.destination)
