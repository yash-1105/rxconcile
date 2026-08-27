"""Google Cloud access layer.

Everything here is Gemini-on-Vertex via Application Default Credentials. No
Cloud Vision, no Document AI, no service account key files, no Cloud Storage --
image bytes are passed inline.
"""

from rxconcile.gcp.client import get_client, reset_client
from rxconcile.gcp.errors import ModelResolutionError, VertexUnavailableError
from rxconcile.gcp.health import HealthSnapshot, health_snapshot
from rxconcile.gcp.models import assert_models_resolve, list_available_models
from rxconcile.gcp.retry import GenerationResult, generate_content

__all__ = [
    "GenerationResult",
    "HealthSnapshot",
    "ModelResolutionError",
    "VertexUnavailableError",
    "assert_models_resolve",
    "generate_content",
    "get_client",
    "health_snapshot",
    "list_available_models",
    "reset_client",
]
