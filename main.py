from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

app = FastAPI()
HTML_DIR = Path(__file__).resolve().parent / "html-css"


def html_response(file_path: Path):
	def endpoint():
		return FileResponse(file_path, media_type="text/html")

	return endpoint


# The landing page is available at the root URL.
index_file = HTML_DIR / "index.html"
app.add_api_route("/", html_response(index_file), methods=["GET"], name="home")

# Expose all other HTML files using their paths relative to html-css.
for file_path in HTML_DIR.rglob("*.html"):
	relative_path = file_path.relative_to(HTML_DIR).as_posix()
	if relative_path == "index.html":
		app.add_api_route(
			"/index.html",
			html_response(file_path),
			methods=["GET"],
			name="index_html",
		)
		continue

	app.add_api_route(
		f"/{relative_path}",
		html_response(file_path),
		methods=["GET"],
		name=f"html_{relative_path.replace('/', '_').replace('.', '_')}",
	)


# Serve the shared stylesheet for both root-relative and nested-page URLs.
stylesheet_file = HTML_DIR / "style.css"


def stylesheet_response(path: str):
	if Path(path).name != "style.css":
		raise HTTPException(status_code=404)
	return FileResponse(stylesheet_file, media_type="text/css")


app.add_api_route("/{path:path}", stylesheet_response, methods=["GET"], name="stylesheet")
