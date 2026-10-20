from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from python.userLoginYRegist import (
	DuplicateUsernameError,
	InvalidCredentialsError,
	login_user,
	register_user,
)

app = FastAPI()
HTML_DIR = Path(__file__).resolve().parent / "html-css"


class Credentials(BaseModel):
	username: str = Field(min_length=1, max_length=64)
	password: str = Field(min_length=1, max_length=1024)


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

	app.add_api_route(
		f"/{relative_path}",
		html_response(file_path),
		methods=["GET"],
		name=f"html_{relative_path.replace('/', '_').replace('.', '_')}",
	)


@app.post("/login")
def login(credentials: Credentials):
	try:
		user = login_user(credentials.username, credentials.password)
	except InvalidCredentialsError as error:
		raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.") from error
	return {"message": "Inicio de sesión exitoso.", "user": user}


@app.post("/register", status_code=201)
def register(credentials: Credentials):
	try:
		user = register_user(credentials.username, credentials.password)
	except DuplicateUsernameError as error:
		raise HTTPException(status_code=409, detail="El nombre de usuario ya está registrado.") from error
	except ValueError as error:
		raise HTTPException(status_code=422, detail=str(error)) from error
	return {"message": "Usuario registrado correctamente.", "user": user}


# Serve the shared stylesheet for both root-relative and nested-page URLs.
stylesheet_file = HTML_DIR / "style.css"


def stylesheet_response(path: str):
	if Path(path).name != "style.css":
		raise HTTPException(status_code=404)
	return FileResponse(stylesheet_file, media_type="text/css")


app.add_api_route("/{path:path}", stylesheet_response, methods=["GET"], name="stylesheet")
