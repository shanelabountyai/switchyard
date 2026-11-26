"""Provider adapters. Every provider in this project is a fake with scripted behaviour."""

from switchyard.providers.base import Provider, ProviderResult
from switchyard.providers.fake import FakeProvider, ScriptError, ScriptWindow

__all__ = ["FakeProvider", "Provider", "ProviderResult", "ScriptError", "ScriptWindow"]
