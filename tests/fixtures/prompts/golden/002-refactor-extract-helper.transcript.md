# Session transcript — refactor: extract shared validation helper

User: I have validation logic duplicated across three request handlers. Clean it up.

Assistant: Let me find the duplication.

Assistant: The same `if not isinstance(payload, dict) or "id" not in payload:` check appears in `handle_create`, `handle_update`, and `handle_delete`. Let me extract a `_validate_payload(payload)` helper.

Assistant: Extracted `_validate_payload` into `validators.py`. The three handlers now call it. Added a test that asserts each handler rejects a missing `id` field.

User: Should I make it raise or return a bool?

Assistant: Raise `ValueError` with a clear message — the handlers already catch `ValueError` to produce 400 responses, so raising keeps the call sites clean (`_validate_payload(payload)` with no `if`). Returning a bool would force every caller to write an `if not valid: return 400` branch, which is the duplication you're trying to avoid.

Assistant: Ran the test suite — green. The refactor removed 24 lines and the handlers are now 6 lines each instead of 14.
