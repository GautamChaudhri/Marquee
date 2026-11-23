"""Marquee configuration: the only package that reads the environment."""

from __future__ import annotations

from marquee.config.app import Settings, settings
from marquee.config.pipeline import PipelineSettings, pipeline_settings

__all__ = ["PipelineSettings", "Settings", "pipeline_settings", "settings"]
