## 2025-05-15 - [Path Traversal in Product Import]
**Vulnerability:** The product import preview view exposed full absolute filesystem paths to the client in a hidden form field and accepted them back for processing. An attacker could modify this field to perform path traversal (e.g., using `../../`) to read or delete arbitrary files on the server when the confirmation step was executed.
**Learning:** Passing internal system details like absolute paths to the client creates a significant security risk and exposes the server's directory structure.
**Prevention:** Always use relative filenames or indirect identifiers (like UUIDs) when communicating with the client about files. On the server, validate and normalize paths using `os.path.normpath` and ensure they remain within strictly defined subdirectories before joining them with the system's root media path.

## 2025-05-22 - [Cross-Store Tag Hijacking in Bulk Import]
**Vulnerability:** The ESL tag import process lacked ownership validation for existing MAC addresses. A user in one store could "hijack" a tag belonging to another store (including across different companies) by including its MAC address in an import file. The system would reassign the tag to the user's current store and gateway without verification.
**Learning:** Globally unique identifiers (like MAC addresses) must be validated against the current tenant/store context before allowing updates, even if they already exist in the database.
**Prevention:** In multi-tenant systems, always verify that existing records retrieved by unique keys belong to the active tenant/context before performing any state changes or reassignments.
