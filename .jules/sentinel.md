## 2025-05-15 - [Path Traversal in Product Import]
**Vulnerability:** The product import preview view exposed full absolute filesystem paths to the client in a hidden form field and accepted them back for processing. An attacker could modify this field to perform path traversal (e.g., using `../../`) to read or delete arbitrary files on the server when the confirmation step was executed.
**Learning:** Passing internal system details like absolute paths to the client creates a significant security risk and exposes the server's directory structure.
**Prevention:** Always use relative filenames or indirect identifiers (like UUIDs) when communicating with the client about files. On the server, validate and normalize paths using `os.path.normpath` and ensure they remain within strictly defined subdirectories before joining them with the system's root media path.

## 2025-05-22 - [Cross-Store Tag Hijacking in Bulk Import]
**Vulnerability:** The ESL tag import process lacked ownership validation for existing MAC addresses. A user in one store could "hijack" a tag belonging to another store (including across different companies) by including its MAC address in an import file. The system would reassign the tag to the user's current store and gateway without verification.
**Learning:** Globally unique identifiers (like MAC addresses) must be validated against the current tenant/store context before allowing updates, even if they already exist in the database.
**Prevention:** In multi-tenant systems, always verify that existing records retrieved by unique keys belong to the active tenant/context before performing any state changes or reassignments.

## 2026-03-07 - [IDOR in Manual Tag Sync]
**Vulnerability:** The 'manual_sync_view' in ESLTagAdmin accepted an object ID and triggered a background task without verifying that the object belonged to the user's authorized store/company. An authenticated user could trigger sync tasks for any tag in the system by manually crafting the URL.
**Learning:** Custom admin views that bypass the standard Django Admin 'change' flow must explicitly re-validate object ownership using the filtered queryset, especially in multi-tenant environments.
**Prevention:** Always use 'self.get_queryset(request).filter(pk=object_id).exists()' (or similar) in custom admin actions and views to ensure the requested object is within the user's allowed scope.

## 2026-03-25 - [Cross-Store Data Hijacking in MQTT Heartbeats]
**Vulnerability:** The `handle_tag_heartbeat` method in `core/mqtt_client.py` performed a bulk lookup of tags by MAC address without filtering by the gateway's store. In a multi-tenant environment where the same MAC can exist in different stores, a heartbeat from one store's gateway could update or hijack tags in another store.
**Learning:** Even automated background processes triggered by hardware signals must enforce tenant isolation. MAC addresses are not globally unique across stores in this system's architecture.
**Prevention:** Always include the tenant context (e.g., `store=gateway.store`) in database queries, even when the trigger is a trusted hardware message, to prevent accidental or malicious cross-tenant data leakage.
