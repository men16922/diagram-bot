"""Install the pinned upstream draw.io web assets outside the repository."""
import hashlib
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path
from zipfile import ZipFile

VERSION = "32.4.1"
SHA256 = "b83663313ccdecef6581476a7eaa96750bccf1bfdc3768981eb5d95e94be7820"
URL = f"https://github.com/jgraph/drawio/releases/download/v{VERSION}/draw.war"


def editor_path():
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "diagram-bot" / "drawio" / VERSION


def install_editor(archive=None, destination=None):
    destination = Path(destination or editor_path())
    if (destination / ".verified-sha256").is_file():
        if (destination / ".verified-sha256").read_text() == SHA256:
            return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staging = Path(temporary)
        source = Path(archive) if archive else staging / "draw.war"
        if not archive:
            with urllib.request.urlopen(URL, timeout=120) as response, source.open("wb") as output:
                shutil.copyfileobj(response, output)
        with source.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
                raise ValueError("draw.io 배포물의 SHA-256이 일치하지 않습니다.")
        web = staging / "web"
        web.mkdir()
        with ZipFile(source) as package:
            for info in package.infolist():
                if info.filename.split("/")[0] in ("WEB-INF", "META-INF"):
                    continue
                target = (web / info.filename).resolve()
                if not target.is_relative_to(web.resolve()):
                    raise ValueError("잘못된 draw.io 배포물 경로")
                package.extract(info, web)
        (web / ".verified-sha256").write_text(SHA256)
        if destination.exists():
            shutil.rmtree(destination)
        web.rename(destination)
    return destination
