"""Bundled web console assets.

The ``static/`` subdirectory contains the HTML, CSS, JavaScript, and font
files for the EverOS console. These are served by the FastAPI application
at ``/console`` when ``settings.server.console_enabled`` is true.

This package is intentionally minimal — no code, just assets accessed via
``importlib.resources``.
"""
