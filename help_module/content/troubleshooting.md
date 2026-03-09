# Troubleshooting & Common Errors

This guide helps you identify and resolve common issues you might encounter while using the SAIS ESL platform.

## 1. Quick Troubleshooting Check

If a tag is not updating as expected, follow these steps:

1. **Check Gateway Status**: Ensure the Gateway is online and active on the **Gateways** page.
2. **Check Tag Battery**: Ensure the tag has sufficient battery level (>20%).
3. **Check Pairing**: Ensure the tag is correctly linked to the intended product.
4. **Manual Sync**: Try clicking the **SYNC** button on the ESL Tags list page to force an update.

![ESL Tag List](/static/help_module/images/tag_list.png)
*Use the SYNC button to manually trigger an update for a specific tag.*

## 2. Standard Error Reference

Below is a list of common errors shown in the "Sync Status" column on the ESL Tags page:

| Error State | Possible Cause | Recommended Solution |
| :--- | :--- | :--- |
| **GEN_FAILED** | Missing product data or invalid hardware spec. | Ensure the product has a price and the tag has a hardware model assigned. |
| **PUSH_FAILED** | Gateway is offline or MQTT broker is unreachable. | Check the gateway's power and internet connection. Verify its status on the Gateways page. |
| **Store Mismatch** | Attempting to link a product from Store A to a tag in Store B. | Ensure both the product and the tag/gateway belong to the same store using the store selector. |
| **No Pending Tasks** | The tag is idle and waiting for a product change. | Change the product price or name to trigger an automatic update, or use the SYNC button. |

## 3. Product Import Troubleshooting (Modisoft)

When importing a Modisoft price book, you might encounter issues:

- **Duplicate Data**: The system will warn you if multiple rows in your file have the same SKU. The last occurrence in the file will typically be used.
- **Missing Price**: Rows without a valid price will be rejected.
- **Invalid SKU**: Ensure your SKUs match the ones expected by the system.

![Modisoft Import Page](/static/help_module/images/modisoft_import.png)
*Always review the import preview for any error messages before confirming the upload.*

## 4. Hardware Connectivity Issues

If multiple tags in the same area are not updating:

- **Gateway Offline**: If the Gateway appears "OFFLINE" on the dashboard, it cannot send updates to any tags.
- **Interference**: Ensure no metal objects are blocking the signal between the gateway and the tags.
- **Range**: Tags must be within the specified range of their assigned gateway.

![Gateway Infrastructure](/static/help_module/images/gateway_list.png)
*The Gateway status page shows which communication hubs are online.*

## 5. Getting Further Help

If you've followed these steps and the issue persists, please contact your system administrator with the following details:
- The Store name and location.
- The MAC address of the affected tag(s).
- The specific error message shown in the platform.
