## 2026-03-25 - Masking Gateway Credentials in Admin and Logs
**Vulnerability:** Gateway credentials (username/password) were visible in plain text in the Django Admin UI and stored in plain text within MQTT audit logs.
**Learning:** Security-sensitive fields in Django Admin should use `PasswordInput(render_value=False)` and require custom `save_model` logic to prevent accidental overwrites when left blank. Audit logs for binary protocols (like MQTT with eStation) require recursive sanitization that handles both key-value pairs and positional list-based parameters.
**Prevention:** Always use secure widgets for credentials in Admin. Implement a centralized sanitization utility for all communication logging.
