"""Integrations package."""

from .ollama_client import LLMUnavailableError, OllamaClient

__all__ = ["OllamaClient", "LLMUnavailableError"]
