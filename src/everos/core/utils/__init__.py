"""Stateless helpers every layer may import directly (one capability per module).

Modules:
    datetime: the two-zone (UTC storage / display-timezone) datetime helpers.
    ids: the path-safe id alphabet and the episode memory-id grammar.
    language: the output language extracted text is written in.
    open_files: the process's open-file soft limit, raised moderately at start.
"""
