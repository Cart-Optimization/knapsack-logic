const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('http://localhost:8000');
  
  const cta = await page.$('.cta');
  const box = await cta.boundingBox();
  console.log("CTA bounding box:", box);
  
  // click it
  await page.click('.cta');
  console.log("Clicked! URL is now:", page.url());
  
  await browser.close();
})();
