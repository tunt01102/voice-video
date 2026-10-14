"""MediaForge — local voice, subtitle, image and video generation service."""

__version__ = "0.1.0"


def main() -> None:
    from mediaforge.cli import app

    app()
