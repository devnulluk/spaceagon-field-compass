"""Build a small badge-install ZIP with the desktop preview and instructions."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BADGE_FILES = (
    "__init__.py", "app.py", "compass_math.py", "compass_view.py",
    "metadata.json", "tildagon.toml",
)
EXTRAS = ("README.md", "LICENSE", "install.ps1", "preview.html", "preview.png", "preview.svg")


def package():
    sources = [(ROOT / ("dev/metadata.json" if name == "metadata.json" else name),
                "spaceagon_compass/" + name) for name in BADGE_FILES]
    sources += [(ROOT / name, name) for name in EXTRAS]
    for source, _ in sources:
        if not source.is_file():
            raise FileNotFoundError("Required release file is missing: " + str(source))
    directory = ROOT / "release"
    directory.mkdir(exist_ok=True)
    target = directory / "spaceagon-field-compass.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for source, destination in sources:
            archive.write(source, destination)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(sources)
        assert "spaceagon_compass/metadata.json" in archive.namelist()
    print(str(target))
    print("Verified ZIP: %d files, %d bytes" % (len(sources), target.stat().st_size))


if __name__ == "__main__":
    package()
