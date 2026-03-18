import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto('http://localhost:8000/admin/login/')
        await page.fill('input[name="username"]', 'admin')
        await page.fill('input[name="password"]', 'admin123')
        await page.click('input[type="submit"]')
        await page.wait_for_url('**/admin/')

        # Go to ESL Tags list view
        await page.goto('http://localhost:8000/admin/core/esltag/')
        await page.wait_for_selector('#nav-sidebar')
        await page.screenshot(path='/home/jules/verification/esltag_list.png')

        # Go to Products list view to check filters
        await page.goto('http://localhost:8000/admin/core/product/')
        await page.screenshot(path='/home/jules/verification/product_list.png')

        # Go to MQTT Messages list view
        await page.goto('http://localhost:8000/admin/core/mqttmessage/')
        await page.screenshot(path='/home/jules/verification/mqtt_list.png')

        await browser.close()

asyncio.run(run())
