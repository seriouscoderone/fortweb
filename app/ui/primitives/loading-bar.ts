/**
 * Indeterminate loading bar (`.loading-bar`) that moves on a timer.
 *
 * It is deliberately not a CSS animation. A running animation makes the page
 * draw a frame on every screen refresh for as long as it runs. On a device that
 * renders in software, that drawing competes with the wallet runtime while it
 * boots: on the Android API 36 CI emulator it pushed the boot past its 30 s
 * budget. The timer draws a few frames a second and nothing in between, and it
 * stops as soon as no bar is showing.
 */

/** Positions the bar's marker moves through before starting again. */
export const LOADING_BAR_STEPS = 4;

/** Time between moves. Three moves a second reads as activity and costs little. */
export const LOADING_BAR_STEP_MS = 300;

let timer: number | null = null;

function showingBars(): HTMLElement[] {
    return Array.from(document.querySelectorAll<HTMLElement>(".loading-bar"))
        .filter((bar) => bar.getClientRects().length > 0);
}

function tick(): void {
    const bars = showingBars();
    if (bars.length === 0) {
        if (timer !== null) window.clearInterval(timer);
        timer = null;
        return;
    }
    for (const bar of bars) {
        const current = Number(bar.dataset["step"] ?? "0");
        bar.dataset["step"] = String((current + 1) % LOADING_BAR_STEPS);
    }
}

/**
 * Start moving every loading bar that is showing. Call it after rendering or
 * revealing one. People who ask for reduced motion get a still bar (see the
 * stylesheet), so no timer runs for them.
 */
export function runLoadingBars(): void {
    if (timer !== null) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    timer = window.setInterval(tick, LOADING_BAR_STEP_MS);
}
