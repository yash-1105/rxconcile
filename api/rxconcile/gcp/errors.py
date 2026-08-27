"""Exceptions raised by the Google Cloud access layer."""

from __future__ import annotations


class GcpError(RuntimeError):
    """Base class for rxconcile Google Cloud failures."""


class ModelResolutionError(GcpError):
    """A configured model does not resolve against this project.

    Raised at boot by :func:`rxconcile.gcp.models.assert_models_resolve`. Preview
    model IDs are withdrawn without notice, so this is deliberately a startup
    failure rather than a first-request failure.
    """


class VertexUnavailableError(GcpError):
    """Vertex could not serve a request after retries and model fallback."""
