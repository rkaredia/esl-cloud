# Product to Tag Linkage

Linkage (or Pairing) is the process of connecting a physical ESL tag to a specific product.

## How to Link
1. Go to the **ESL Tags** section.
2. Select a tag.
3. In the "Paired Product" field, search for the product by name or SKU.
4. Save the tag. The system will automatically generate the ESL image and push it to the tag.

![Tag Pairing Detail](/static/help_module/images/tag_pairing.png)
*Select the product you want to display on the physical tag and save your changes.*

## Bulk Mapping
For large-scale setups, use the **Bulk Map** feature:
1. Upload a text file with a list of `TagMAC,ProductSKU` pairs.
2. The system will validate the pairs and show a preview.
3. Confirm to link all pairs at once.

### Human Error Pitfalls in Linkage
- **Mapping Errors**: The system will warn you if a SKU is not found in your product list. Ensure you've imported your products first.
- **Multiple Tags per Product**: It is possible to link multiple tags to the same product (e.g., for different shelves).
- **Multiple Products per Tag**: A single tag can only be linked to one product at a time. Linking a new product will replace the old one.

---
*Next: [Troubleshooting](troubleshooting)*
