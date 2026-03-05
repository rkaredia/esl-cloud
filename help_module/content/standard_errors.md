# Standard Errors

Common errors you might encounter:

## GEN_FAILED (Image Generation Failed)
- **Cause**: Usually caused by missing product data or an invalid hardware specification.
- **Solution**: Ensure the product has a price and the tag has a hardware model assigned.

## PUSH_FAILED (Gateway Delivery Failed)
- **Cause**: The gateway is offline or the MQTT broker is unreachable.
- **Solution**: Check the gateway's internet connection and power.

## Store Mismatch
- **Cause**: Attempting to link a product from Store A to a tag assigned to a gateway in Store B.
- **Solution**: Ensure both the product and the tag/gateway belong to the same store.
