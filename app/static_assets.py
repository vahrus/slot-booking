"""Content-based static CSS versions shared by public and admin templates."""

from hashlib import sha256
from pathlib import Path


STYLESHEET_PATH = Path(__file__).resolve().parent / "static" / "style.css"


def stylesheet_version() -> str:
    """Change automatically with CSS bytes, including in Docker deployments."""
    return sha256(STYLESHEET_PATH.read_bytes()).hexdigest()[:16]
