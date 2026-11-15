import { expect, test } from '@playwright/test';

import { finishRequest, setUpWallet, startRequest } from './fixtures/roles';

const NO_SUCH_EXCHANGE = `E${'A'.repeat(43)}`;

test.describe('The credential engine in this build', () => {
    test('is switched on: an exchange that does not exist is not found, where a build without the switch says unsupported',
        async ({ page }) => {
            await setUpWallet(page);
            // Only one runtime at a time may hold the wallet's storage, so the page's own gives the vault up first.
            await page.getByRole('button', { name: 'Lock vault' }).click();
            await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible({ timeout: 240_000 });
            await startRequest(page, 'vaults.open', { vaultId: 'my-wallet', passcode: 'roles-pass-1' });
            expect((await finishRequest(page)).ok).toBe(true);

            await startRequest(page, 'ipex.get', { vaultId: 'my-wallet', syntheticOnly: true, said: NO_SUCH_EXCHANGE });
            expect(await finishRequest(page)).toMatchObject({ ok: false, code: 'NOT_FOUND' });

            // The engine still asks every caller to say that the credentials are test ones.
            await startRequest(page, 'ipex.get', { vaultId: 'my-wallet', said: NO_SUCH_EXCHANGE });
            expect(await finishRequest(page)).toMatchObject({ ok: false, code: 'UNSUPPORTED' });
        });
});
