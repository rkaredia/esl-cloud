## 2026-03-25 - Masking Gateway Credentials in Admin and Logs
**Vulnerability:** Gateway credentials (username/password) were visible in plain text in the Django Admin UI and stored in plain text within MQTT audit logs.
**Learning:** Security-sensitive fields in Django Admin should use `PasswordInput(render_value=False)` and require custom `save_model` logic to prevent accidental overwrites when left blank. Audit logs for binary protocols (like MQTT with eStation) require recursive sanitization that handles both key-value pairs and positional list-based parameters.
**Prevention:** Always use secure widgets for credentials in Admin. Implement a centralized sanitization utility for all communication logging.

## 2026-04-09 - Enforcing Granular RBAC in Custom Admin Actions and Imports
**Vulnerability:** Custom Django Admin actions (like 'safe_delete') and multi-step import views (like 'preview_tag_import') were accessible to staff users without verifying the specific underlying permissions (e.g., 'delete_esltag', 'add_esltag').
**Learning:** Standard Django permission decorators or mixins on views/Admin classes often only protect the entry point. Custom logic that performs bulk operations or uses helpers like 'get_or_create' must explicitly verify granular permissions to prevent privilege escalation by 'Read-Only' or limited staff users.
**Prevention:** Always wrap data-modifying logic in custom actions and views with explicit 'request.user.has_perm()' checks. Differentiate between 'add' and 'change' permissions in import processes to restrict creation vs. modification.
