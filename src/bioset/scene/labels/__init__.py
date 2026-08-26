"""Label placement for mesh surfaces.

`manager.LabelSceneManager` is the entry point; `flagpole` is the vendored
solver and `sites` adapts bioset's meshes and colocalization into it.
"""
from .manager import LabelSceneManager, parse_label_keys

__all__ = ["LabelSceneManager", "parse_label_keys"]
