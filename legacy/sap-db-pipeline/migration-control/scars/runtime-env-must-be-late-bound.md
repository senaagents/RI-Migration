# Runtime Environment Must Be Late Bound

## Trigger

A diagnostic wrapper injected SAP settings after importing pipeline modules. The first config implementation had captured environment defaults at import time, so the command saw missing SAP credentials.

## Migration Impact

Migration runs will use wrappers, scheduled commands, and site-config derived environment variables. If settings are captured too early, a command can silently target the wrong staging database or fail to connect.

## Rule

Read runtime settings inside `get_settings()` at command execution time.

Do not capture SAP credentials, schema, or `DATABASE_URL` as import-time defaults.
