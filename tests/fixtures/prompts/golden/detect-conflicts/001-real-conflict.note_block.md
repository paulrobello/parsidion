### note-a
SQLite WAL mode recommendation
Enable WAL mode on the database to avoid database-is-locked errors under concurrent writers.

### note-b
Rollback journal preference
Never use WAL mode; always keep the rollback journal for reliable crash recovery and simpler backups.

### note-c
SQLite prepared statements
Use prepared statements to speed up repeated queries and avoid SQL injection.
