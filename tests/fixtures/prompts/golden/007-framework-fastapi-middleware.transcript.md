# Session transcript — FastAPI middleware ordering bug

User: My FastAPI auth middleware runs AFTER the CORS middleware and rejects preflight requests. Fix?

Assistant: The order matters. FastAPI/Starlette runs middleware in LIFO order for the request phase — the LAST middleware added is the FIRST to see the request. So if CORS is added after auth, CORS sees preflight OPTIONS first and short-circuits (correct). If auth is added after CORS, auth sees the OPTIONS request first and rejects it (wrong).

Fix: add CORS middleware LAST so it's first in the request path:

    app.add_middleware(AuthMiddleware)
    app.add_middleware(CORSMiddleware, ...)

This is counter-intuitive — the call order is the REVERSE of the execution order on the way in.

User: Is this documented?

Assistant: Buried in Starlette's docs. The mental model: middleware form a stack. Each middleware wraps everything inside it. The outermost wrapper (last added) runs first on the request, last on the response.

Verified the fix locally — preflight OPTIONS now returns 204 from CORS without hitting auth.
