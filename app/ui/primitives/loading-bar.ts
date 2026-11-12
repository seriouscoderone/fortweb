/**
 * Indeterminate loading bar (`.loading-bar`) that moves on a timer.
 *
 * It is deliberately not a CSS animation. A running animation makes the page
 * draw a frame on every screen refresh for as long as it runs. On a device that
 * renders in software, that drawing competes with the wallet runtime while it
 * boots: on the Android API 36 CI emulator it pushed the boot past its 30 s
 * budget. The timer draws one frame a second and nothing in between, and it
 * stops as soon as no bar is showing.
 */

/** Positions the bar's marker moves through before starting again. */
export const LOADING_BAR_STEPS = 4;

/**
 * Time between moves. Every move is one drawn frame, and on the CI emulator a
 * frame is expensive: at three moves a second the startup proof passed with
 * about a second of its 30 s budget left. One move a second still reads as
 * activity and leaves the boot nearly undisturbed.
 */
export const LOADING_BAR_STEP_MS = 1000;

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
