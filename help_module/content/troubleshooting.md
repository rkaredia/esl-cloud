# Troubleshooting

If tags are not updating, check the following:

## 1. Gateway Status
Ensure the Gateway is online and active. Gateways act as the bridge between the SAIS platform and the physical tags.

## 2. MQTT Connectivity
The system uses MQTT to communicate with gateways. Check the system logs if you suspect connection issues.

## 3. Celery Workers
Image generation and transmission happen in the background via Celery. If updates are "Pending" for a long time, the Celery workers might be down.

## 4. Manual Sync
You can force a tag update by clicking the **SYNC** button on the ESL Tags list page.
