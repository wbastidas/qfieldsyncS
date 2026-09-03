"""Lectores de geodatabase de qfieldESRI."""

from .base import GeodatabaseReader, ReaderError, get_reader

__all__ = ["GeodatabaseReader", "ReaderError", "get_reader"]
