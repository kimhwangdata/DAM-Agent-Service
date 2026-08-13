"""Build and publish the dam-ffmpeg Lambda layer — plan 3.2, ADR-0005.

Downloads the johnvansickle static ffmpeg release build (x86_64, matching
the Lambda architecture), verifies it against the md5 published alongside
it, extracts the single `ffmpeg` binary into `bin/ffmpeg`, and publishes
the zip as a new version of the `dam-ffmpeg` layer.

After the first successful run, pin EXPECTED_SHA256 below to the printed
digest so future rebuilds are reproducible-or-fail.

Run:  python scripts/aws/build_ffmpeg_layer.py   (profile knh-dev)
"""

from __future__ import annotations

import hashlib
import io
import lzma
import tarfile
import urllib.request
import zipfile

import boto3

PROFILE = "knh-dev"
REGION = "ap-northeast-2"
LAYER_NAME = "dam-ffmpeg"
BASE = "https://johnvansickle.com/ffmpeg/releases"
TARBALL = "ffmpeg-release-amd64-static.tar.xz"
# Pinned from the first verified download (2026-08-13).
EXPECTED_SHA256: str | None = (
    "abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67"
)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "dam-builder"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def main() -> None:
    print(f"downloading {BASE}/{TARBALL} ...")
    tar_bytes = fetch(f"{BASE}/{TARBALL}")
    sha256 = hashlib.sha256(tar_bytes).hexdigest()
    print(f"downloaded {len(tar_bytes) / 1e6:.1f} MB  sha256={sha256}")

    if EXPECTED_SHA256 is not None:
        if sha256 != EXPECTED_SHA256:
            raise SystemExit("FATAL: sha256 mismatch against the pinned digest")
        print("[ok] pinned sha256 verified")
    else:
        md5_line = fetch(f"{BASE}/{TARBALL}.md5").decode().split()[0]
        md5 = hashlib.md5(tar_bytes).hexdigest()
        if md5 != md5_line:
            raise SystemExit("FATAL: md5 mismatch against the published checksum")
        print("[ok] published md5 verified - pin the sha256 above for the future")

    print("extracting ffmpeg binary ...")
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(tar_bytes))) as tar:
        member = next(
            m for m in tar.getmembers() if m.name.endswith("/ffmpeg") and m.isfile()
        )
        version_dir = member.name.split("/", 1)[0]
        ffmpeg_bytes = tar.extractfile(member).read()  # type: ignore[union-attr]
    print(f"[ok] {version_dir}  binary {len(ffmpeg_bytes) / 1e6:.1f} MB")

    layer_zip = io.BytesIO()
    with zipfile.ZipFile(layer_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("bin/ffmpeg")
        info.external_attr = 0o755 << 16  # executable
        info.compress_type = zipfile.ZIP_DEFLATED  # ZipInfo defaults to STORED
        zf.writestr(info, ffmpeg_bytes)
    print(f"layer zip {layer_zip.getbuffer().nbytes / 1e6:.1f} MB")

    lam = boto3.Session(profile_name=PROFILE, region_name=REGION).client("lambda")
    out = lam.publish_layer_version(
        LayerName=LAYER_NAME,
        Description=f"static ffmpeg ({version_dir}) sha256={sha256[:16]}",
        Content={"ZipFile": layer_zip.getvalue()},
        CompatibleRuntimes=["python3.12"],
        CompatibleArchitectures=["x86_64"],
    )
    print(f"[published] {out['LayerVersionArn']}")
    print("DONE")


if __name__ == "__main__":
    main()
