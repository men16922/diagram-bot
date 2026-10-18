"""The installed package version, without importing the CLI (and through it the collector and report)."""
from importlib import metadata


def app_version():
    try:
        return metadata.version("diagram-bot")
    except metadata.PackageNotFoundError:
        return "dev"
