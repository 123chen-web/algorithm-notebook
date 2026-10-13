"""Route handlers split out of main.py, one module per domain.

main.py keeps: app creation, middleware, lifespan, exception handlers,
route registration (include_router calls), and startup initialization.

Shared helpers live in their owning modules (core/, models/, db, and the
top-level domain modules); routers reference them as ``<module>.<name>``
(attribute access at call time), so ``monkeypatch.setattr(<module>, ...)``
in tests keeps affecting the routed code.
"""
