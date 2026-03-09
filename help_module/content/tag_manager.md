# Tag Manager

The Tag Manager is where you register and manage your Electronic Shelf Labels (ESL).

## Registering Tags
- **Single Registration**: Add a tag by providing its MAC address and selecting its hardware model.
- **Bulk Import**: Use the Excel template to import hundreds of tags at once.

![ESL Tag List](/static/help_module/images/tag_list.png)
*The ESL Tag list provides a central view of all tags in the system.*

## Hardware Specifications
Each tag is associated with a hardware model (e.g., ET0213-81). These specifications define the screen resolution and color capabilities (B&W, BWR, BWRY).

## Battery Monitoring
The system tracks the battery level of each tag. A low battery warning (red bar) is shown when the level drops below 20%.

![Battery Level](/static/help_module/images/tag_list.png)
*Check the "Battery" column for a visual indicator of each tag's status.*

### Human Error Pitfalls in Tag Management
- **Incorrect MAC Address**: If you enter the wrong MAC address, the physical tag will not receive updates.
- **Incorrect Hardware Spec**: Using the wrong hardware model will cause image generation to fail because the resolution will be incorrect for the physical screen.

---
*Next: [Product to Tag Linkage](linkage)*
