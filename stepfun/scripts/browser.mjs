import { chromium } from 'playwright';
export const baseURL = process.env.APP_URL || 'http://127.0.0.1:4177';
export async function openBrowser({ gpu = false } = {}) {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
    args: ['--no-sandbox', ...(gpu
      ? ['--enable-unsafe-webgpu', ...(process.env.SOFTWARE_GPU === '1' ? ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] : [])]
      : ['--disable-gpu', '--disable-features=WebGPU'])],
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
  // All inference, models, scripts and data are same-origin. Fail on remote API/asset requests.
  const remoteRequests = [];
  await context.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith(baseURL + '/') || /^(data|blob):/.test(url)) return route.continue();
    remoteRequests.push(url); return route.abort();
  });
  const errors = [];
  const page = await context.newPage();
  page.setDefaultTimeout(180000);
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(baseURL);
  await page.waitForFunction(() => window.kb);
  await page.evaluate(() => window.kb.ready);
  const environment = await page.evaluate(async () => {
    const adapter = await navigator.gpu?.requestAdapter();
    return { browser: navigator.userAgent, threads: navigator.hardwareConcurrency,
      crossOriginIsolated, gpu: adapter ? { features: [...adapter.features],
        vendor: adapter.info?.vendor, architecture: adapter.info?.architecture,
        device: adapter.info?.device, description: adapter.info?.description } : null };
  });
  return { browser, context, page, remoteRequests, errors, environment };
}
