"""Package the static index and the media it references for a classroom PC."""

import json
from html.parser import HTMLParser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from cjvmt.config import Settings
from cjvmt.demo import export_site


class ReplayIndex(HTMLParser):
    def __init__(self, page: Path):
        super().__init__()
        self.reading = False
        self.data = None
        self.feed(page.read_text(encoding="utf-8"))

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "app-data":
            self.reading = True

    def handle_data(self, data):
        if self.reading:
            self.data = json.loads(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.reading = False


def package(settings: Settings) -> Path:
    page = export_site(settings)
    root = page.parent
    assets = {page, root / "app.js", root / "app.css"}
    index = ReplayIndex(page).data
    assets.update(root / item["path"] for item in index["materials"])
    assets.update((root / "figures").glob("*.png"))
    for run in index["runs"]:
        assets.update(root / route for route in (run["video"], *run["download"].values()))
        assets.update(root / run["base"] / row["clip"]["path_preview"] for row in run["lines"] if "clip" in row)
        if run["summary"]:
            assets.add(root / run["latency_csv"])
    destination = root / "cj-vmt-offline.zip"
    temporary = destination.with_suffix(".tmp")
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(assets):
                bundle.write(path, path.relative_to(root))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    print(package(Settings.load()))
