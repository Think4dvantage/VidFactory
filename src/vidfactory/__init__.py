from importlib.metadata import PackageNotFoundError, version

# Single source of truth is pyproject.toml; the Docker image installs the package, so its
# metadata is always present there. Only a bare source checkout (PYTHONPATH=src) lacks it.
try:
    __version__ = version("vidfactory")
except PackageNotFoundError:
    __version__ = "0+unknown"
