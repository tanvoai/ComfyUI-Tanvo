"""Tanvo for ComfyUI: https://github.com/tanvoai/ComfyUI-Tanvo"""
from .tanvo_nodes import comfy_entrypoint

WEB_DIRECTORY = "./js"

__all__ = ["comfy_entrypoint", "WEB_DIRECTORY"]
