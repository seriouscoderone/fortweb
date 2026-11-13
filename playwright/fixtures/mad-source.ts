import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { BrowserContext } from '@playwright/test';

// The fixture source of python/fixtures/mad served at https://mad.example.test/, with no real server:
// Playwright's context.route also answers fetches made by the wallet's worker.

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const FIXTURES = path.join(REPO, 'python', 'fixtures', 'mad');
const LIVE_SOURCE = 'https://mad.seriouscoder.one/source.json';
export const FIXTURE_HOST = 'mad.example.test';
export const FIXTURE_SOURCE = `https://${FIXTURE_HOST}/source.json`;
const NOT_THE_WALLET = 'https://not-the-wallet.example.test';
const DEFINITION_PATH = /^\/definitions\/(E[A-Za-z0-9_-]{43})\.json$/;

export interface FixtureSourceOptions {
    /** false: answer without allowing the wallet's origin, so the wallet may not read it. */
    cors?: boolean;
    /** false: answer the definitions (only) without allowing the wallet's origin. */
    definitionsCors?: boolean;
    /** The content type the definitions are served with (default application/json). */
    definitionType?: string;
    /** Serve this definition with one byte changed. */
    tamper?: string;
    /** Serve a source document of 1,000,001 bytes. */
    oversize?: boolean;
    /** Serve every definition as 1,000,001 bytes. */
    oversizeDefinitions?: boolean;
    /** Answer source.json with 404, with a 200 text/html page, or with a 302 to another address on the host. */
    sourceAnswer?: 'missing' | 'html' | 'redirect';
    /** Hold the answer to source.json: `arrived` is called when its request comes in, the answer waits for `release`. */
    holdSource?: { arrived(): void; release: Promise<void> };
    /** Change the parsed source document before it is served (e.g. a publication's name, or the source's `by`). */
    edit?(document: Record<string, unknown>): void;
}

export function fixtureSourceDocument(): Buffer {
    const text = readFileSync(path.join(FIXTURES, 'source.json'), 'utf8');
    if (!text.includes(`"${LIVE_SOURCE}"`)) {
        throw new Error('fixture source.json no longer names the live source');
    }
    return Buffer.from(text.replace(`"${LIVE_SOURCE}"`, `"${FIXTURE_SOURCE}"`), 'utf8');
}

/** One byte changed: the first lowercase letter after the SAID's own occurrence becomes uppercase. */
export function tampered(bytes: Buffer, said: string): Buffer {
    const copy = Buffer.from(bytes);
    const start = copy.indexOf(said) + said.length;
    for (let i = Math.max(start, 0); i < copy.length; i += 1) {
        if (copy[i] >= 0x61 && copy[i] <= 0x7a) {
            copy[i] -= 0x20;
            return copy;
        }
    }
    throw new Error(`cannot tamper with ${said}`);
}

function definitions(): Map<string, Buffer> {
    const dir = path.join(FIXTURES, 'compact');
    return new Map(readdirSync(dir).filter((name) => name.endsWith('.json'))
        .map((name) => [name.slice(0, -'.json'.length), readFileSync(path.join(dir, name))]));
}

/** What the wallet has asked the fixture source for. */
export interface FixtureSourceRequests {
    /** The SAID of every definition requested so far, in the order the requests arrived. */
    definitions: string[];
}

/** The bytes padded with trailing spaces to 1,000,001 bytes, so that their size is their only fault. */
function oversized(bytes: Buffer): Buffer {
    return Buffer.concat([bytes, Buffer.alloc(1_000_001 - bytes.length, 0x20)]);
}

export async function routeFixtureSource(
    context: BrowserContext, options: FixtureSourceOptions = {},
): Promise<FixtureSourceRequests> {
    let valid = fixtureSourceDocument();
    if (options.edit) {
        const parsed = JSON.parse(valid.toString('utf8')) as Record<string, unknown>;
        options.edit(parsed);
        valid = Buffer.from(JSON.stringify(parsed), 'utf8');
    }
    const document = options.oversize ? oversized(valid) : valid;
    const served = definitions();
    const requests: FixtureSourceRequests = { definitions: [] };
    if (options.tamper !== undefined) {
        const original = served.get(options.tamper);
        if (original === undefined) {
            throw new Error(`no fixture definition ${options.tamper}`);
        }
        served.set(options.tamper, tampered(original, options.tamper));
    }
    // route.fulfill adds access-control-allow-origin: <request origin> when the header is missing
    // (playwright-core server/network.js _maybeAddCorsHeaders), so a source that does not allow the wallet is
    // answered with an origin that is not the wallet's: to the browser that is the same refusal.
    const headers: Record<string, string> = {
        'content-type': 'application/json',
        'access-control-allow-origin': options.cors === false ? NOT_THE_WALLET : '*',
    };
    await context.route(`https://${FIXTURE_HOST}/**`, async (route) => {
        const { pathname } = new URL(route.request().url());
        if (pathname === '/source.json') {
            if (options.holdSource) {
                options.holdSource.arrived();
                await options.holdSource.release;
            }
            if (options.sourceAnswer === 'missing') {
                await route.fulfill({ status: 404, headers, body: '' });
            } else if (options.sourceAnswer === 'html') {
                await route.fulfill({ status: 200, headers: { ...headers, 'content-type': 'text/html' },
                    body: '<!doctype html><title>x</title>' });
            } else if (options.sourceAnswer === 'redirect') {
                await route.fulfill({ status: 302, headers: { ...headers, location: '/moved/source.json' }, body: '' });
            } else {
                await route.fulfill({ status: 200, headers, body: document });
            }
            return;
        }
        const said = DEFINITION_PATH.exec(pathname)?.[1];
        if (said !== undefined) {
            requests.definitions.push(said);
        }
        const body = said === undefined ? undefined : served.get(said);
        if (body === undefined) {
            await route.fulfill({ status: 404, headers, body: '' });
            return;
        }
        const refused = options.definitionsCors === false ? { 'access-control-allow-origin': NOT_THE_WALLET } : {};
        const type = options.definitionType === undefined ? {} : { 'content-type': options.definitionType };
        await route.fulfill({ status: 200, headers: { ...headers, ...refused, ...type },
            body: options.oversizeDefinitions ? oversized(body) : body });
    });
    return requests;
}
