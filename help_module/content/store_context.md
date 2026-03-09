# Store Context & Isolation

The SAIS ESL platform is built with multi-tenant isolation in mind. This means each store's data is completely separate and secure.

## Active Store Selection
When you first log in, you will be prompted to select a store. Once selected, all pages and lists will be filtered to only show data for that specific store.

![Store Selector](/static/help_module/images/dashboard.png)
*Use the store selector at the top right to choose the store you are working in.*

## Key Isolation Rules
- **Products**: Products from one store cannot be linked to tags in another store.
- **Gateways**: Each gateway is assigned to a specific store.
- **ESL Tags**: Each tag is assigned to a specific store and gateway.
- **Users**: Some users might have access to only one store, while others can manage multiple stores.

## Switching Stores
You can switch stores at any time using the store selector in the top right corner of the admin header.

---
*Next: [Troubleshooting](troubleshooting)*
