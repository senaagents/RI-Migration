# ERPNext Custom DocType DDL Can Outlive Rollbacks

## Scar

While applying SAP accounting dimensions, the first real run failed during generated custom-field creation. The database transaction was rolled back, but the custom DocType table and some generated custom fields had already persisted.

## Migration Lesson

ERPNext/Frappe metadata operations can trigger MariaDB DDL, and DDL may auto-commit independently of the surrounding application transaction. A failed setup script can therefore leave a partial metadata state even when the Python code calls `frappe.db.rollback()`.

## Operational Rule

- Treat setup scripts that create DocTypes, Custom Fields, Property Setters, or schema as idempotent from the start.
- Check `DocType`, target tables, and `Custom Field` records before every create.
- Re-run a verification dry-run after schema setup; it should report zero remaining changes.
- Do not assume rollback removed metadata/schema artifacts.
