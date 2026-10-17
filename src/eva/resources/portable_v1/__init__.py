"""Exact supported portable-v1 resources, independent of checkout layout."""
from importlib.resources import as_file, files


def schema_resources():
    """Keep packaged schema files available for bounded descriptor-based reads."""
    return as_file(files(__package__).joinpath("schemas"))


def sealed_freeze_bytes() -> bytes:
    return files(__package__).joinpath("schema-freeze-v1.json").read_bytes()
