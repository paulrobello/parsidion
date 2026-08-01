### note-a
FastAPI middleware order
Register middleware in the order it should execute, outermost first.

### note-b
FastAPI dependency overrides
Use dependency_overrides to swap providers during tests without touching routes.

### note-c
FastAPI route handler shape
Keep route handlers thin and push business logic into a service layer.
