"""Replaceable generator interface. Path C is registered by default."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from PIL import Image


class Generator(ABC):
    """Swappable construction backend. No live model in this slice."""

    name: str
    construction_path: str

    @abstractmethod
    def construct(
        self,
        spec: dict[str, Any],
        palette: dict[str, Any],
        style: dict[str, Any],
        *,
        template_text: str | None = None,
    ) -> Image.Image:
        """Return a native-resolution RGBA key pose using palette roles only."""

    def construct_idle(
        self,
        key_frame: Image.Image,
        spec: dict[str, Any],
        palette: dict[str, Any],
        style: dict[str, Any],
    ) -> list[Image.Image]:
        """Optional idle frames from a key pose. Default: not implemented."""
        raise NotImplementedError(
            f"{self.name} does not implement idle animation"
        )


_REGISTRY: dict[str, type[Generator]] = {}


def register_generator(path: str, cls: type[Generator]) -> None:
    _REGISTRY[path] = cls


def get_generator(construction_path: str) -> Generator:
    cls = _REGISTRY.get(construction_path)
    if cls is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(
            f"No generator registered for construction_path={construction_path!r}. "
            f"Known: {known}"
        )
    return cls()


def available_paths() -> list[str]:
    return sorted(_REGISTRY)
