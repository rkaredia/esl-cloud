# Store Context & Isolation

The SAIS platform is designed to support multiple stores while keeping data strictly isolated.

## Active Store Selection
You must select an **Active Store** from the header. Once selected:
- All product lists will only show products for that store.
- All tag lists will only show tags for that store.
- Import and Export operations will only affect that store.

## Security
Isolation is enforced at the database level. Even if a user has access to multiple stores, they only work with one at a time to prevent accidental cross-store updates.
