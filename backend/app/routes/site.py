"""Static file serving for the single-service deployment.

Locally the stack runs nginx in front of `frontend/` and Flask on its own port.
A free hosting tier gives one web service, so in production the same Flask
process serves both the pages and the API. Doing it here rather than adding a
second service keeps the deployment to one moving part -- and, more usefully,
puts the frontend and the API on the **same origin**, which removes CORS from
the deployed system entirely.

The blueprint registers only when `FRONTEND_DIR` exists, so a backend-only
container (or a developer running `python run.py` from a checkout without the
frontend built into the image) behaves exactly as before.
"""

from pathlib import Path

from flask import Blueprint, current_app, send_from_directory

bp = Blueprint("site", __name__)

#: Where the frontend lives relative to the backend package. Two candidates:
#: the repository layout (`../frontend`) and the Docker image layout, where the
#: Dockerfile copies it in beside the application code.
FRONTEND_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "frontend",   # backend/app/routes -> backend/frontend
    Path(__file__).resolve().parents[3] / "frontend",   # repo checkout: <repo>/frontend
)


def frontend_dir():
    """Return the directory holding the frontend, or None when absent.

    Returns:
        A `Path` to the first candidate that exists and contains `index.html`,
        or None -- in which case this blueprint is not registered and the
        service answers only the API.
    """
    for candidate in FRONTEND_CANDIDATES:
        if (candidate / "index.html").is_file():
            return candidate
    return None


@bp.get("/")
def index():
    """Serve the map page at the site root."""
    return send_from_directory(current_app.config["FRONTEND_DIR"], "index.html")


@bp.get("/<path:filename>")
def static_file(filename: str):
    """Serve any other frontend file.

    `send_from_directory` resolves the path against the frontend directory and
    refuses anything that escapes it, so a request for `../backend/.env` is a
    404 rather than a disclosure.

    Args:
        filename: Path requested by the browser, relative to the frontend root.

    Returns:
        The file, or 404.
    """
    return send_from_directory(current_app.config["FRONTEND_DIR"], filename)
