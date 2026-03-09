# Product Management

This module allows you to manage the products available in your stores.

## Features
- **Manual Entry**: Add products one by one through the admin interface.
- **Modisoft Import**: Export your price book from Modisoft and import it here to update prices and names in bulk.
- **Automated Sync**: When a product's price, name, or supplier changes, any linked ESL tags will be automatically updated.

![Product List](/static/help_module/images/product_list.png)
*Use the "Add Product" button for manual entry or "Import Modisoft" for bulk updates.*

## Modisoft Import Process
1. Export your price book from Modisoft as an Excel file.
2. Navigate to the **Products** page in the SAIS platform.
3. Click on the **Import Modisoft** button.
4. Upload your file and preview the changes.
5. **Confirm the import** to apply changes to your store.

![Modisoft Import Page](/static/help_module/images/modisoft_import.png)
*Upload your Modisoft Excel file here to sync your price book in bulk.*

### Human Error Pitfalls in Product Management
- **Incorrect SKU**: If you enter the wrong SKU for a product, the system will not be able to link it to the correct tag.
- **Empty Price**: Products without prices cannot be linked to tags (Image Generation will fail).
- **Incomplete Import**: If you close the page before the import is complete, some products might not be updated.

---
*Next: [Tag Manager](tag-manager)*
