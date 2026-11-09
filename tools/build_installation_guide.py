"""Build Korean or English guides with Chrome via the existing Playwright CLI."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from string import Template
import subprocess
import threading

from markdown_it import MarkdownIt


GUIDE_NAME = "한인교회 통역 시스템 처음 셋업 (SKC-translate)"


def prepare(source: Path, output: Path, root: Path, language: str) -> tuple[Path, Path, Path]:
    work = root / ".agent/scratch/installation-guide-print"
    work.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).with_name("templates") / "installation_guide.html"
    body = MarkdownIt("commonmark", {"html": False}).render(source.read_text(encoding="utf-8"))
    html = work / "guide.html"
    title = "SKC Live Translation - Setup and First Use" if language == "en" else "SKC Live Translation - 처음 설치와 사용 안내"
    html.write_text(Template(template.read_text(encoding="utf-8")).substitute(content=body, language=language, title=title), encoding="utf-8")
    config = work / "playwright.json"
    config.write_text(json.dumps({
        "browser": {"browserName": "chromium", "userDataDir": str(root / ".agent/cache/playwright/pdf-profile"),
                    "launchOptions": {"channel": "chrome", "headless": True}},
        "outputDir": str(work / "browser-artifacts"), "outputMode": "file",
    }), encoding="utf-8")
    render = work / "print.js"
    footer = ('<div style="font:8px Arial;width:100%;text-align:center;color:#666">'
              '<span class="pageNumber"></span> / <span class="totalPages"></span></div>')
    render.write_text("async page => {\n"
                      "  await page.evaluate(() => document.fonts.ready);\n"
                      "  await page.emulateMedia({media: 'print'});\n"
                      "  await page.pdf({path: " + json.dumps(str(output.resolve())) + ",\n"
                      "    format: 'Letter', preferCSSPageSize: true, printBackground: true,\n"
                      "    displayHeaderFooter: true, headerTemplate: '<span></span>',\n"
                      "    footerTemplate: " + json.dumps(footer) + "});\n"
                      "  return {printed: true};\n}\n", encoding="utf-8")
    return html, config, render


def build(source: Path, output: Path, root: Path, *, language: str = "ko", html_only: bool = False) -> Path:
    html, config, render = prepare(source, output, root, language)
    if html_only:
        return html
    launcher = shutil.which("playwright-cli.cmd")
    node = shutil.which("node")
    if launcher is None or node is None:
        raise RuntimeError("Install the Playwright CLI and Chrome before building this PDF.")
    cli = Path(launcher).parent / "node_modules/@playwright/cli/playwright-cli.js"
    if not cli.is_file():
        raise FileNotFoundError(cli)
    env = dict(os.environ, PWTEST_DAEMON_SESSION_DIR=str(config.parent / "daemon"))
    command = [node, str(cli), "-s=skc-guide-pdf"]

    def run(*arguments: str) -> None:
        result = subprocess.run(command + list(arguments), cwd=root, env=env, text=True,
                                encoding="utf-8", errors="replace", capture_output=True, check=True)
        if "### Error" in result.stdout:
            raise RuntimeError(result.stdout)

    output.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(html.parent)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        run("open", f"http://127.0.0.1:{server.server_port}/guide.html", "--config=" + str(config))
        try:
            run("run-code", "--filename=" + str(render))
        finally:
            run("close")
    finally:
        server.shutdown()
        server.server_close()
    return output


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("ko", "en"), default="ko")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--html-only", action="store_true", help="Prepare the browser preview without printing.")
    args = parser.parse_args()
    source_name = "SKC_Live_Translation_Setup_Guide_EN.md" if args.language == "en" else f"{GUIDE_NAME}.md"
    output_name = "SKC_Live_Translation_Setup_Guide_EN.pdf" if args.language == "en" else f"{GUIDE_NAME}.pdf"
    print(build(args.source or root / "docs" / source_name, args.output or root / "docs" / output_name,
                root, language=args.language, html_only=args.html_only))
