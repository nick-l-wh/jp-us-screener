"""Package the Vite build and its public data into a locally openable HTML file."""

import base64
import json
import re
import sys
from pathlib import Path


def package(dist: Path, output: Path, days: list[dict] | None = None) -> None:
    html = (dist / "index.html").read_text(encoding="utf-8")

    def built_asset(match: re.Match) -> Path:
        return dist / "assets" / Path(match[1]).name

    def script(match: re.Match) -> str:
        js = built_asset(match).read_text(encoding="utf-8").replace("</script", "<\\/script")
        return f'<script type="module">{js}</script>'

    def style(match: re.Match) -> str:
        css = built_asset(match).read_text(encoding="utf-8")
        return f"<style>{css}</style>"

    html, scripts = re.subn(r'<script type="module"[^>]+src="([^"]+)"[^>]*></script>', script, html)
    html, styles = re.subn(r'<link rel="stylesheet"[^>]+href="([^"]+)"[^>]*>', style, html)
    if (scripts, styles) != (1, 1):
        raise ValueError("Expected one bundled script and one stylesheet in dist/index.html")

    embedded = []
    if days:
        payload = json.dumps(days, ensure_ascii=False).replace("<", "\\u003c")
        embedded.append(f'<script type="application/json" id="bundled-trading-days">{payload}</script>')
    for path in sorted(dist.rglob("*")):
        mime = {".csv": "text/csv", ".json": "application/json"}.get(path.suffix)
        if mime:
            key = path.relative_to(dist).as_posix()
            data = path.read_bytes()
            value = base64.b64encode(data).decode("ascii")
            embedded.append(f'<script type="text/plain" id="asset:{key}">data:{mime};base64,{value}</script>')
    output.write_text(html.replace("</body>", "\n".join(embedded) + "\n</body>"), encoding="utf-8")


if __name__ == "__main__":
    days = [json.loads(Path(arg).read_text(encoding="utf-8-sig")) for arg in sys.argv[2:]]
    package(Path(__file__).with_name("dist"), Path(sys.argv[1]), days)
