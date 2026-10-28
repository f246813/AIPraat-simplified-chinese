import {expect, test} from '@playwright/test';
import {writeFileSync} from 'node:fs';
import {join} from 'node:path';

/**
 * Cost profile of one long-history read-through.
 *
 * The numbers behind the virtualisation contract are measured rather than inferred: the mount stays
 * a viewport slice while the whole logical range is browsed, a burst of native scroll notifications
 * becomes at most one application batch per frame, no pass enumerates every message row, and the
 * browser's own layout and scripting time is accounted for the trajectory. Engine batch durations are
 * read off the frame callback that runs them, so the measurement follows the coordinator's own work.
 */
type Cost = {scrollEvents: number; batches: number[]; frames: number[]; longTasks: number[]; rowScans: number[]; layoutReads: number};
type Sample = {mounted: number; nodes: number; scrollTop: number; firstId: string; lastId: string};

const percentile = (values: number[], fraction: number): number => {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))];
};

test('a read-through of long history keeps the mount a slice and the frame work in one batch', async ({page, context}) => {
  test.setTimeout(120000);
  await page.addInitScript(() => {
    const cost: Cost = {scrollEvents: 0, batches: [], frames: [], longTasks: [], rowScans: [], layoutReads: 0};
    (window as unknown as {__cost: Cost}).__cost = cost;
    // Every notification the app layer could turn into work, counted apart from the batches it
    // actually runs: coalescing only holds while the batch count stays below the event count.
    window.addEventListener('scroll', () => { cost.scrollEvents++; }, true);
    // Enumerating every message row is the structural failure this design forbids, so any selector
    // for all rows is recorded together with how many rows it found.
    for (const prototype of [Document.prototype, Element.prototype]) {
      const queryAll = prototype.querySelectorAll;
      prototype.querySelectorAll = function(this: Document & Element, selector: string) {
        const nodes = queryAll.call(this, selector);
        if (selector.includes('[data-message-id]')) cost.rowScans.push(nodes.length);
        return nodes;
      } as typeof prototype.querySelectorAll;
    }
    const rect = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function(this: Element) { cost.layoutReads++; return rect.call(this); } as typeof Element.prototype.getBoundingClientRect;
    const frame = window.requestAnimationFrame.bind(window);
    // Attribute the per-frame work by the callback's own source: `runBatch` is the engine's single
    // per-frame entry, so this times that batch instead of an unrelated callback.
    window.requestAnimationFrame = (callback: FrameRequestCallback) => frame((time: number) => {
      const started = performance.now();
      try { callback(time); } finally {
        if (callback.name === 'transcriptViewportBatch') cost.batches.push(performance.now() - started);
      }
    });
    let previous = 0;
    const tick = (time: number) => { if (previous) cost.frames.push(time - previous); previous = time; frame(tick); };
    frame(tick);
    new PerformanceObserver(list => { for (const entry of list.getEntries()) cost.longTasks.push(entry.duration); }).observe({entryTypes: ['longtask']});
  });
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('.session-select').filter({hasText: '长历史'}).click();
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();

  const transcript = page.locator('.transcript');
  const box = (await transcript.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  // Only the mounted rows are in the DOM, so the row count and the document's node count are the
  // direct measure of how much the reader's trip through the history costs the page.
  const sample = async (): Promise<Sample> => transcript.evaluate(el => {
    const rows = Array.from(el.querySelectorAll('.message'));
    return {mounted: rows.length, nodes: document.getElementsByTagName('*').length, scrollTop: Math.round(el.scrollTop),
      firstId: (rows[0] as HTMLElement)?.dataset.messageId || '', lastId: (rows[rows.length - 1] as HTMLElement)?.dataset.messageId || ''};
  });
  const samples: Sample[] = [await sample()];

  const cdp = await context.newCDPSession(page);
  await cdp.send('Performance.enable');
  const getMetrics = (method: string) => (cdp.send as unknown as (method: string, params?: object) => Promise<{metrics: {name: string; value: number}[]}>)(method);
  const counters = async () => Object.fromEntries((await getMetrics('Performance.getMetrics')).metrics.map(entry => [entry.name, entry.value])) as Record<string, number>;
  const before = await counters();

  // Real wheel input from the tail to the withheld head, then back down. Every page of history the
  // reader reaches adds rows above the reading line and the engine holds that line, so the trip is
  // measured by the rows reached rather than a fixed number of steps.
  let upSteps = 0;
  for (; upSteps < 150; upSteps++) {
    await page.mouse.wheel(0, -400); await page.waitForTimeout(20); samples.push(await sample());
    if (samples[samples.length - 1].firstId === 'history-0') break;
  }
  let downSteps = 0;
  for (; downSteps < 150; downSteps++) {
    await page.mouse.wheel(0, 400); await page.waitForTimeout(20); samples.push(await sample());
    if (samples[samples.length - 1].lastId === 'diagram-fixture') break;
  }
  const tailReached = samples[samples.length - 1];

  // A burst with nothing awaited between the wheel events is where a self-written scroll loop would
  // multiply its work: the notifications and the batches the app layer actually runs are counted
  // across the burst alone.
  const burstBefore = await page.evaluate(() => { const c = (window as unknown as {__cost: Cost}).__cost; return {events: c.scrollEvents, batches: c.batches.length}; });
  for (let step = 0; step < 24; step++) void page.mouse.wheel(0, -300);
  await page.waitForTimeout(600);
  const burstAfter = await page.evaluate(() => { const c = (window as unknown as {__cost: Cost}).__cost; return {events: c.scrollEvents, batches: c.batches.length, lastBatch: c.batches[c.batches.length - 1] ?? 0}; });
  samples.push(await sample());

  // Opening every remaining page of history is the case a virtualised mount is supposed to survive:
  // the logical range becomes the whole session while the DOM stays a viewport slice.
  const reveal = page.locator('.history-reveal button');
  for (let round = 0; round < 12 && await reveal.count(); round++) { await reveal.evaluate(el => (el as HTMLElement).click()); await page.waitForTimeout(120); }
  const fullyExpanded = await sample();
  const after = await counters();

  const cost = await page.evaluate(() => (window as unknown as {__cost: Cost}).__cost);
  const evidence = {
    fixture: '长历史与渲染夹具 · 121 mixed-height messages (formulas, code, tables, diagram) · 1280x850 · Microsoft Edge channel · dev bundle',
    trajectory: {upSteps, downSteps, deltaPx: 400, samples: samples.length},
    mount: {minMounted: Math.min(...samples.map(s => s.mounted)), maxMounted: Math.max(...samples.map(s => s.mounted)), minNodes: Math.min(...samples.map(s => s.nodes)), maxNodes: Math.max(...samples.map(s => s.nodes))},
    fullyExpanded,
    burst: {dispatchedWheelEvents: 24, nativeScrollEvents: burstAfter.events - burstBefore.events, engineBatches: burstAfter.batches - burstBefore.batches, lastBatchMs: Number(burstAfter.lastBatch.toFixed(2))},
    historyReachable: {oldestMounted: samples.some(s => s.firstId === 'history-0'), newestMounted: samples.some(s => s.lastId === 'diagram-fixture')},
    coalescing: {nativeScrollEvents: cost.scrollEvents, engineBatches: cost.batches.length, frames: cost.frames.length},
    batchCostMs: {p50: Number(percentile(cost.batches, .5).toFixed(2)), p95: Number(percentile(cost.batches, .95).toFixed(2)), max: Number(Math.max(...cost.batches, 0).toFixed(2))},
    frameIntervalMs: {p50: Number(percentile(cost.frames, .5).toFixed(2)), p95: Number(percentile(cost.frames, .95).toFixed(2)), max: Number(Math.max(...cost.frames, 0).toFixed(2))},
    longTasks: {count: cost.longTasks.length, totalMs: Number(cost.longTasks.reduce((a, b) => a + b, 0).toFixed(0)), maxMs: Number(Math.max(...cost.longTasks, 0).toFixed(0))},
    wholeHistoryRowScans: cost.rowScans,
    layoutReads: {count: cost.layoutReads, note: 'includes the probe wrapper around getBoundingClientRect'},
    browserCounters: Object.fromEntries(Object.entries(after).filter(([name]) => /Duration|Count$/.test(name)).map(([name, value]) => [name, Number((value - (before[name] || 0)).toFixed(3))])),
    tailSample: tailReached,
  };
  await test.info().attach('viewport-cost', {body: JSON.stringify(evidence, null, 2), contentType: 'application/json'});
  if (process.env.PI_SCRATCH_DIR) writeFileSync(join(process.env.PI_SCRATCH_DIR, 'frontend-viewport-cost.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({mount: evidence.mount, fullyExpanded: evidence.fullyExpanded, burst: evidence.burst, reachable: evidence.historyReachable, coalescing: evidence.coalescing, batchCostMs: evidence.batchCostMs, longTasks: evidence.longTasks, scans: evidence.wholeHistoryRowScans, counters: evidence.browserCounters}, null, 1));

  // Browsing the whole range never grows the DOM with it: rows stay a viewport slice, and both ends
  // of the history were reached during the trip.
  expect(evidence.mount.maxMounted).toBeLessThanOrEqual(40);
  expect(evidence.historyReachable).toEqual({oldestMounted: true, newestMounted: true});
  expect(tailReached.lastId).toBe('diagram-fixture');
  // Expanding the logical range to the whole session keeps the mount bounded: the count of mounted
  // messages stays the viewport's business, not the number of messages the reader has opened.
  expect(fullyExpanded.mounted).toBeLessThanOrEqual(40);
  // A burst of native scroll notifications yields at most one batch per frame, and no pass
  // enumerated every message row.
  expect(cost.batches.length).toBeGreaterThan(0);
  expect(cost.batches.length).toBeLessThanOrEqual(Math.max(cost.frames.length, 1));
  expect(cost.rowScans).toEqual([]);
});
