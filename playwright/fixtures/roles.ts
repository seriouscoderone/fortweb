import { expect, type Page } from '@playwright/test';
import { FIXTURE_HOST } from './mad-source.js';

// Helpers shared by the Roles specs (fortweb-roles*.spec.ts).

export const CLASS_BRICK = 'EDVEy5suX6WPROMl6vSc5Kc4I86mXxyFsa_xBla4A_dY';
export const DROPIN_CLASS = 'EFQ6ngR2kib-dwmW8mizJ4k5IdO0YysG0xSty9ESHDE4';
export const MEMBER_CLASS = 'EJfIK_QhDYXcEpciRPUZhVc4ChUEh2EJLAZfn5veZUqL';
export const CLASS_ATTENDED = 'EO6exNffu9clQv9czAMDzj9W_CNW3W47yAJhMqYC05Qz'; // an rr of class-brick; all three use it
export const PAID_ADMISSION = 'EIjETqo6CwKdjLKiUbve977olj92nuhBoy4NPywpqD1U'; // an rr only dropin-class uses

export interface Publication {
    target: string;
    name: string;
    title: string;
    roles: Array<{ role: string; responsibilities: Array<Record<string, unknown>> }>;
    problem: string | null;
}

export interface ReadResult {
    source: { address: string; host: string };
    checked: number;
    dropped: { entries: number; publications: number };
    publications: Publication[];
}

export type Outcome =
    | { ok: true; result: ReadResult & Record<string, unknown> }
    | { ok: false; code: string; message: string };

export async function setUpWallet(page: Page): Promise<void> {
    await page.goto('/fortweb/app/index.html');
    await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible({ timeout: 240_000 });
    await page.getByLabel('Passcode', { exact: true }).fill('roles-pass-1');
    await page.getByLabel('Confirm passcode', { exact: true }).fill('roles-pass-1');
    await page.getByRole('button', { name: 'Create wallet' }).click();
    await expect(page.getByRole('heading', { name: 'Roles', exact: true, level: 1 })).toBeVisible({ timeout: 180_000 });
}

export async function openStartPage(page: Page): Promise<void> {
    await page.goto('/fortweb/app/index.html');
    await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible({ timeout: 240_000 });
}

export interface TestWindow {
    rolesTestBridge?: { request(method: string, params: object): Promise<unknown> };
    rolesPendingRead?: Promise<Outcome>;
    rolesPendingReadSettled?: boolean;
}

/** Starts a request on the test's bridge (made on first use) and keeps its outcome on window.rolesPendingRead. */
export async function startRequest(page: Page, method: string, params: object): Promise<void> {
    await page.evaluate(async ([name, args]) => {
        const holder = window as unknown as TestWindow;
        if (!holder.rolesTestBridge) {
            const bridgeModule = '/fortweb/app/runtime/bridge.js';
            const { createRuntimeBridge } = await import(bridgeModule);
            holder.rolesTestBridge = createRuntimeBridge({
                workerUrl: new URL('/fortweb/app/runtime/wallet-worker.py', window.location.href),
                configUrl: new URL('/fortweb/pyscript-ci.toml', window.location.href),
                runtimeOriginContract: null,
            });
        }
        holder.rolesPendingReadSettled = false;
        holder.rolesPendingRead = holder.rolesTestBridge.request(name as string, args as object).then(
            (result) => ({ ok: true, result }) as Outcome,
            (error: { code?: string; message?: string }) =>
                ({ ok: false, code: String(error.code), message: String(error.message) }) as Outcome,
        ).finally(() => { holder.rolesPendingReadSettled = true; });
    }, [method, params] as const);
}

export async function finishRequest(page: Page): Promise<Outcome> {
    return page.evaluate(() => (window as unknown as TestWindow).rolesPendingRead as Promise<Outcome>);
}

/** One more request on the same bridge, answered within `withinMs` or reported as code 'STILL_WAITING'. */
export async function requestWithin(page: Page, method: string, withinMs: number): Promise<{ ok: boolean; code?: string }> {
    return page.evaluate(async ([name, limit]) => {
        const bridge = (window as unknown as TestWindow).rolesTestBridge;
        if (!bridge) throw new Error('no test bridge yet');
        let timer = 0;
        const late = new Promise<{ ok: boolean; code?: string }>((resolve) => {
            timer = window.setTimeout(() => resolve({ ok: false, code: 'STILL_WAITING' }), limit as number);
        });
        const answered = bridge.request(name as string, {}).then(
            () => ({ ok: true }),
            (error: { code?: string }) => ({ ok: false, code: String(error.code) }),
        );
        try {
            return await Promise.race([answered, late]);
        } finally {
            window.clearTimeout(timer);
        }
    }, [method, withinMs] as const);
}

export async function readSource(page: Page, address: string): Promise<Outcome> {
    await startRequest(page, 'sources.read', { address });
    return finishRequest(page);
}

export function byTarget(outcome: Outcome): Record<string, Publication> {
    if (!outcome.ok) {
        throw new Error(`sources.read failed: ${outcome.code} ${outcome.message}`);
    }
    return Object.fromEntries(outcome.result.publications.map((p) => [p.target, p]));
}

export const NOT_AN_ADDRESS = 'That is not an address the wallet can open.';
export const ONE_DEFINITION = 'ECru8k3CerlcJF0Khe0EOubRaA4U4APPGqOdvkzNR0L1'; // served, refers to nothing
export const MARKUP = '<img src=x onerror=alert(1)>';

export type SourceDocument = Record<string, unknown> & { publications: Array<Record<string, unknown>>; serves: unknown[] };

export function editSource(change: (document: SourceDocument) => void): (document: Record<string, unknown>) => void {
    return (document) => change(document as SourceDocument);
}

/** A held answer to source.json: `arrived` resolves when its request comes in; `release()` lets it through. */
export function heldSource(): { option: { arrived(): void; release: Promise<void> }; arrived: Promise<void>; release(): void; count(): number } {
    let requests = 0;
    let arrive!: () => void;
    const arrived = new Promise<void>((resolve) => { arrive = resolve; });
    let release!: () => void;
    const released = new Promise<void>((resolve) => { release = resolve; });
    return {
        option: { arrived() { requests += 1; arrive(); }, release: released },
        arrived,
        release,
        count: () => requests,
    };
}

export function rolesPage(page: Page) {
    const main = page.getByRole('main');
    return {
        main,
        address: main.getByRole('textbox', { name: 'Address' }),
        open: main.getByRole('button', { name: 'Open', exact: true }),
        status: main.locator('[data-roles-status]'),
        announcer: page.locator('#fw-live-region'),
        host: main.getByRole('heading', { level: 2, name: FIXTURE_HOST, exact: true }),
        summary: main.locator('[data-roles-summary]'),
        cards: main.getByRole('article'),
        card: (name: string) => main.getByRole('article', { name, exact: true }),
    };
}

export async function openAddress(page: Page, address: string): Promise<void> {
    const roles = rolesPage(page);
    await roles.address.fill(address);
    await roles.open.click();
}

export const NOT_APPOINTED_SENTENCE = 'Nobody has granted this identifier the role. This wallet cannot hold grants yet.';
export const OPEN_AGAIN = 'Open the address again before taking on a role.';
export const ALREADY_TAKEN = 'This identifier has already taken on that role.';
export const CASHIER_DUTIES = [
    'payment-received: relies on remittance-submitted (as remittance); attests payment-received; '
    + 'produces payment-received',
    'paid-admission: relies on payment-received (as payment); produces admission',
];

/** Opens the fixture source and shows the roles of dropin-class ("Drop-in takes a class"). */
export async function showDropinRoles(page: Page): Promise<void> {
    const roles = rolesPage(page);
    await openAddress(page, FIXTURE_HOST);
    await roles.card('dropin-class').getByRole('button', { name: 'Show roles' }).click({ timeout: 90_000 });
}

export function myRoles(page: Page) {
    const region = page.getByRole('main').getByRole('region', { name: 'My roles' });
    return {
        region,
        items: region.getByRole('listitem').filter({ has: page.getByRole('heading', { level: 3 }) }),
        item: (role: string) => region.getByRole('listitem')
            .filter({ has: page.getByRole('heading', { level: 3, name: role, exact: true }) }),
    };
}

export function takeOnDialog(page: Page, role: string) {
    const dialog = page.getByRole('dialog', { name: `Take on ${role}`, exact: true });
    return {
        dialog,
        createNew: dialog.getByRole('radio', { name: 'Create a new identifier' }),
        selectExisting: dialog.getByRole('radio', { name: 'Select an existing identifier' }),
        name: dialog.getByRole('textbox', { name: 'Name' }),
        existing: dialog.getByRole('combobox', { name: 'Identifier' }),
        takeOn: dialog.getByRole('button', { name: 'Take on', exact: true }),
        cancel: dialog.getByRole('button', { name: 'Cancel', exact: true }),
        status: dialog.locator('[data-take-on-status]'),
    };
}

/** Clicks "Take on" for `role` in dropin-class (its roles shown) and returns the dialog. */
export async function startTakingOn(page: Page, role: string) {
    await rolesPage(page).card('dropin-class').getByRole('button', { name: `Take on ${role}`, exact: true }).click();
    const dialog = takeOnDialog(page, role);
    await expect(dialog.dialog).toBeVisible();
    return dialog;
}

export async function takeOnWithNewIdentifier(page: Page, role: string): Promise<void> {
    const dialog = await startTakingOn(page, role);
    await dialog.takeOn.click();
    await expect(dialog.dialog).toHaveCount(0, { timeout: 90_000 });
    await expect(myRoles(page).item(role)).toHaveCount(1);
}

export async function unlock(page: Page, passcode = 'roles-pass-1'): Promise<void> {
    await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible({ timeout: 240_000 });
    await page.getByLabel('Passcode').fill(passcode);
    await page.getByRole('button', { name: 'Open', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Roles', exact: true, level: 1 })).toBeVisible({ timeout: 180_000 });
}

/** The menu covers most of a phone's screen; the visible strip of the overlay beside it closes it. */
export async function closePhoneMenu(page: Page): Promise<void> {
    const viewport = page.viewportSize();
    await page.getByRole('button', { name: 'Close navigation' })
        .click({ position: { x: (viewport?.width ?? 412) - 10, y: (viewport?.height ?? 915) / 2 } });
    await expect(page.getByRole('navigation', { name: 'Vault navigation' })).toBeHidden();
}

export async function sendToBackgroundFor31Seconds(page: Page): Promise<void> {
    // As fortweb-wallet-first.spec.ts: hidden, 31 s later visible again; the runtime is discarded.
    await page.evaluate(() => {
        let hidden = false;
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
        hidden = true;
        document.dispatchEvent(new Event('visibilitychange'));
        const realNow = Date.now.bind(Date);
        Date.now = () => realNow() + 31_000;
        hidden = false;
        document.dispatchEvent(new Event('visibilitychange'));
    });
}

/** From now on, every text the live region announces is kept; `announced(page)` gives them (empty ones left out). */
export async function recordAnnouncements(page: Page): Promise<void> {
    await page.evaluate(() => {
        const announced: string[] = [];
        (window as unknown as { announced: string[] }).announced = announced;
        const region = document.querySelector('#fw-live-region');
        if (!region) throw new Error('no live region');
        new MutationObserver(() => announced.push(region.textContent ?? ''))
            .observe(region, { childList: true, characterData: true, subtree: true });
    });
}

export async function announced(page: Page): Promise<string[]> {
    return page.evaluate(() => (window as unknown as { announced: string[] }).announced.filter((text) => text !== ''));
}

interface RuntimeRequestsWindow {
    runtimeRequests: { sent: string[]; waiting: Array<{ method: string; deliver(): void }>; held: boolean };
}

/**
 * Call before the app starts. Records the method of every request the page sends to its runtime worker, and can hold
 * them: the page posts each request as one message on a MessagePort ([n, "handle_request", [request]]), and while
 * the hold is on those messages wait, so a request stays unanswered until `release()` sends them on, in order.
 */
export async function watchRuntimeRequests(page: Page) {
    await page.addInitScript(() => {
        const requests: RuntimeRequestsWindow['runtimeRequests'] = { sent: [], waiting: [], held: false };
        (window as unknown as RuntimeRequestsWindow).runtimeRequests = requests;
        const post = MessagePort.prototype.postMessage as (this: MessagePort, ...args: unknown[]) => void;
        MessagePort.prototype.postMessage = function (this: MessagePort, ...args: unknown[]): void {
            const message = args[0];
            let method = '';
            if (Array.isArray(message) && message[1] === 'handle_request' && Array.isArray(message[2])) {
                try {
                    method = String((JSON.parse(String(message[2][0])) as { method?: unknown }).method ?? '');
                } catch {
                    method = ''; // the readiness probe is not a request
                }
            }
            if (method) {
                requests.sent.push(method);
                if (requests.held) {
                    requests.waiting.push({ method, deliver: () => post.apply(this, args) });
                    return;
                }
            }
            post.apply(this, args);
        };
    });
    return {
        /** How many requests for `method` the page has sent so far; one that is held counts. */
        sent: (method: string) => page.evaluate((name) => (window as unknown as RuntimeRequestsWindow)
            .runtimeRequests.sent.filter((sent) => sent === name).length, method),
        /** How many requests for `method` are waiting behind the hold. */
        held: (method: string) => page.evaluate((name) => (window as unknown as RuntimeRequestsWindow)
            .runtimeRequests.waiting.filter((request) => request.method === name).length, method),
        /** From now on the page's requests wait. */
        hold: () => page.evaluate(() => {
            (window as unknown as RuntimeRequestsWindow).runtimeRequests.held = true;
        }),
        release: () => page.evaluate(() => {
            const requests = (window as unknown as RuntimeRequestsWindow).runtimeRequests;
            requests.held = false;
            for (const request of requests.waiting.splice(0)) request.deliver();
        }),
    };
}
