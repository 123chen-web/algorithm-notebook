"""Route handlers split out of main.py, one module per domain.

main.py keeps: app creation, middleware, lifespan, exception handlers,
route registration (include_router calls at the bottom of main.py), and
all shared helpers / models / constants.

Names living in main's namespace are referenced inside these modules as
``main.<name>`` (attribute access at call time), so
``monkeypatch.setattr(main, ...)`` in tests keeps affecting the moved code.
"""
