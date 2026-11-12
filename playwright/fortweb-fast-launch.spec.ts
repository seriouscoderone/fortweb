import { expect, test, type Page } from '@playwright/test';

// Faster launch: the default vault's fingerprint prompt must not wait for the
// Pyodide runtime to boot. Uses the real runtime (deliberately slowed) and a
// fake Fortoid bridge whose secret store survives reloads (localStorage).

async function installPersistentFakeBridge(page: Page): Promise<void> {
    await page.addInitScript(() => {
        const w = window as unknown as Record<string, unknown>;
        const load = () => JSON.parse(localStorage.getItem('__fakeSecrets') || '{}') as Record<string, string>;
        const save = (s: Record<string, string>) => localStorage.setItem('__fakeSecrets', JSON.stringify(s));
        const listeners: Array<(event: { data: string }) => void> = [];
        const log: Array<{ capability: string; at: number }> = [];
        w['__nativeLog'] = log;
        w['bridge'] = {
            addEventListener(type: string, listener: (event: { data: string }) => void) {
                if (type === 'message') listeners.push(listener);
            },
            postMessage(raw: string) {
                const req = JSON.parse(raw);
                if (req.type !== 'native_request') return;
                log.push({ capability: req.capability, at: performance.now() });
                const store = load();
                const key = req.params?.key;
                let out: Record<string, unknown>;
                if (req.capability === 'secret.status') out = { ok: true, result: { stored: key in store, biometricAvailable: true } };
                else if (req.capability === 'secret.store') { store[key] = req.params.value; save(store); out = { ok: true, result: { stored: true } }; }
                else if (req.capability === 'secret.retrieve') out = key in store ? { ok: true, result: { value: store[key] } } : { ok: false, error: { code: 'bad_request', message: 'none' } };
                else if (req.capability === 'secret.delete') { delete store[key]; save(store); out = { ok: true, result: { deleted: true } }; }
                else out = { ok: false, error: { code: 'unknown_capability', message: req.capability } };
                const response = JSON.stringify({ type: 'native_response', id: req.id, ...out });
                setTimeout(() => listeners.forEach((l) => l({ data: response })), 0);
            },
        };
    });
}

async function slowRuntime(page: Page, delayMs: number): Promise<void> {
    await page.route('**/pyscript-ci.toml', async (route) => {
        await new Promise((resolve) => setTimeout(resolve, delayMs));
        await route.continue();
    });
}

test('the default vault prompts for the fingerprint before the runtime finishes booting', async ({ page }) => {
    test.setTimeout(420_000);
    await installPersistentFakeBridge(page);
    await page.goto('/fortweb/app/index.html');
    await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible({ timeout: 240_000 });
    await page.getByLabel('Passcode', { exact: true }).fill('fast-launch-1');
    await page.getByLabel('Confirm passcode', { exact: true }).fill('fast-launch-1');
    await page.getByRole('button', { name: 'Create wallet' }).click();
    await page.getByRole('dialog').filter({ hasText: 'Use fingerprint' })
        .getByRole('button', { name: 'Use fingerprint', exact: true }).click({ timeout: 180_000 });
    await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible();

    // Relaunch with a runtime that takes 6 s longer to start.
    await slowRuntime(page, 6_000);
    await page.reload();

    // The unlock screen and the fingerprint request appear without waiting for it.
    await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible({ timeout: 3_000 });
    await expect.poll(async () => (await page.evaluate(() =>
        (window as unknown as Record<string, Array<{ capability: string }>>)['__nativeLog']
            .map((entry) => entry.capability))), { timeout: 3_000 }).toContain('secret.retrieve');
    await expect(page.getByRole('progressbar')).toBeVisible();
    await expect(page.getByRole('progressbar')).toHaveAttribute('data-step', /^[1-3]$/, { timeout: 3_000 });

    // The vault opens once the runtime is ready, with exactly one fingerprint request.
    await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible({ timeout: 240_000 });
    const retrieves = await page.evaluate(() =>
        (window as unknown as Record<string, Array<{ capability: string }>>)['__nativeLog']
            .filter((entry) => entry.capability === 'secret.retrieve').length);
    expect(retrieves).toBe(1);
});

test('a starting wallet shows a loading bar', async ({ page }) => {
    test.setTimeout(300_000);
    await slowRuntime(page, 6_000);
    await page.goto('/fortweb/app/index.html');

    const bar = page.getByRole('progressbar', { name: 'Starting your wallet' });
    await expect(bar).toBeVisible({ timeout: 3_000 });
    await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible({ timeout: 240_000 });
    await expect(bar).toHaveCount(0);
});

// A running CSS animation makes the page draw a frame on every screen refresh.
// On a device that renders in software that load competes with the runtime
// while it boots: it pushed the Android API 36 startup proof past the 30 s boot
// budget. The bar therefore moves on a timer, a few steps per second, and
// nothing is drawn in between.
test('the loading bar moves in a few steps per second and runs no animation', async ({ page }) => {
    test.setTimeout(300_000);
    await page.addInitScript(() => {
        // Track the bar's timer (300 ms period) so the test can see it stop.
        const w = window as unknown as Record<string, unknown>;
        const active = new Set<number>();
        w['__barTimers'] = active;
        const realSet = window.setInterval.bind(window);
        const realClear = window.clearInterval.bind(window);
        window.setInterval = ((handler: TimerHandler, delay?: number, ...rest: unknown[]) => {
            const id = realSet(handler, delay, ...rest);
            if (delay === 300) active.add(id);
            return id;
        }) as typeof window.setInterval;
        window.clearInterval = ((id?: number) => {
            if (id !== undefined) active.delete(id);
            realClear(id);
        }) as typeof window.clearInterval;
    });
    await slowRuntime(page, 6_000);
    await page.goto('/fortweb/app/index.html');

    const bar = page.getByRole('progressbar', { name: 'Starting your wallet' });
    await expect(bar).toBeVisible({ timeout: 3_000 });

    // Once the page's one-off fade-in has ended, nothing is animating between
    // the bar's steps.
    await expect.poll(async () => page.evaluate(() => document.getAnimations().length),
        { timeout: 2_000 }).toBe(0);

    // The bar still moves: between 2 and 5 steps a second, through several positions.
    const steps = await bar.evaluate(async (element) => {
        const seen: string[] = [];
        const observer = new MutationObserver(() => seen.push((element as HTMLElement).dataset['step'] ?? ''));
        observer.observe(element, { attributes: true, attributeFilter: ['data-step'] });
        await new Promise((resolve) => setTimeout(resolve, 2_000));
        observer.disconnect();
        return seen;
    });
    expect(steps.length).toBeGreaterThanOrEqual(4);
    expect(steps.length).toBeLessThanOrEqual(10);
    expect(new Set(steps).size).toBeGreaterThanOrEqual(3);

    // Once no bar is showing, its timer stops.
    await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible({ timeout: 240_000 });
    await expect.poll(async () => page.evaluate(() =>
        (window as unknown as Record<string, Set<number>>)['__barTimers'].size), { timeout: 3_000 }).toBe(0);
});

test('a remembered vault that no longer exists falls back to normal launch', async ({ page }) => {
    test.setTimeout(300_000);
    await page.addInitScript(() => {
        localStorage.setItem('fortweb.launch.defaultVault', JSON.stringify({
            id: 'ghost', alias: 'Ghost', createdAt: '2026-01-01T00:00:00Z',
        }));
    });
    await page.goto('/fortweb/app/index.html');

    await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible({ timeout: 240_000 });
    expect(await page.evaluate(() => localStorage.getItem('fortweb.launch.defaultVault'))).toBeNull();
});
