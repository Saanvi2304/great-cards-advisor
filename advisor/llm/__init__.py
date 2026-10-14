"""LLM provider adapters.

Each provider module exposes a single function matching the signature in
``base.py``.  The rest of the application never imports a provider directly;
``base.generate_structured()`` dispatches based on the settings.
"""
