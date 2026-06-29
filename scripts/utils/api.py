"""API key loading utilities."""

import os

from .workspace import load_env


def load_api_key(key_name: str, required: bool = True) -> str:
    """Load an API key from environment or .env file.

    Args:
        key_name: Environment variable name (e.g., 'FIRECRAWL_API_KEY').
        required: If True, raise ValueError when key not found.

    Returns:
        The API key string, or empty string if not required and not found.

    Raises:
        ValueError: If the key is required and not found in the environment or .env file.
    """
    key = os.environ.get(key_name)
    if key:
        return key

    load_env()
    key = os.environ.get(key_name, "")
    if key:
        return key

    if required:
        raise ValueError(
            f"{key_name} not found in .env or environment. "
            f"Set it in .env or export it before running."
        )
    return ""
