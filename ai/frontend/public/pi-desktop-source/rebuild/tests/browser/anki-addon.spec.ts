import {expect, test} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {delimiter, resolve} from 'node:path';

// Render through the actual Anki hook with the audio fixture used by backend tests.
const cards = JSON.parse(execFileSync(process.env.AIPRAAT_TEST_PYTHON || 'python', ['-c', `
import json
from unittest.mock import patch
from test_anki_bridge import AddonTests
fixture = AddonTests()
fixture.setUp()
cards = {}
for mode in ('button', 'magnifier', 'both'):
    with patch.object(fixture.addon, 'config', return_value={'mode': mode, 'magnifier_selector': '.Search'}):
        cards[mode] = fixture.addon.render('<a class="Search" href="goldendict://test">放大镜</a>', fixture.card, 'reviewQuestion')
fixture.card.question_av_tags.return_value = []
fixture.card.answer_av_tags.return_value = []
cards['silent'] = fixture.addon.render('<p>无音频卡片</p>', fixture.card, 'reviewAnswer')
print(json.dumps(cards))
`], {encoding: 'utf8', env: {...process.env, PYTHONPATH: [resolve('..'), resolve('../tests')].join(delimiter)}}));

const reviewerScript = process.env.AIPRAAT_ANKI_REVIEWER_JS
  ? readFileSync(process.env.AIPRAAT_ANKI_REVIEWER_JS, 'utf8') : '';

async function showCard(page: any, mode = 'button') {
  // Anki inserts card HTML and replaces inert script nodes to run them in order.
  await page.evaluate(async (html: string) => {
    if (typeof (window as any)._updateQA === 'function') {
      await (window as any)._updateQA(html, null, () => {}, () => {});
      return;
    }
    const qa = document.getElementById('qa')!;
    qa.innerHTML = html;
    for (const oldScript of qa.getElementsByTagName('script')) {
      const script = document.createElement('script');
      script.textContent = oldScript.textContent;
      oldScript.replaceWith(script);
      await Promise.resolve();
    }
  }, cards[mode]);
}

async function holdButton(page: any) {
  const box = (await page.locator('#aipraat-audio-button').boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await expect(page.locator('#aipraat-audio-button')).toHaveCSS('cursor', 'grabbing');
  return box;
}

test.beforeEach(async ({page}) => {
  await page.setViewportSize({width: 900, height: 650});
  await page.setContent('<html class="win"><body><div id="qa"></div></body></html>');
  await page.evaluate(() => {
    (window as any).events = [];
    (window as any).pycmd = (message: string) => (window as any).events.push(message);
  });
  if (reviewerScript) {
    await page.evaluate(() => {
      (window as any).bridgeCommand = () => {};
      (window as any).MathJax = {startup: {promise: Promise.resolve()}, typesetClear() {}, typesetPromise: () => Promise.resolve()};
    });
    await page.addScriptTag({content: reviewerScript});
  }
  await showCard(page);
});

test('default entry is centered on the left and short click dispatches once', async ({page}) => {
  const button = page.locator('#aipraat-audio-button');
  const box = (await button.boundingBox())!;
  expect(box.x).toBe(12);
  expect(Math.abs(box.y + box.height / 2 - 325)).toBeLessThan(1);
  await button.click();
  expect(await page.evaluate(() => (window as any).events)).toEqual(['aipraat:open']);
});

test('long press drags without opening audio, preserves position between cards, and clamps to the viewport', async ({page}) => {
  const box = await holdButton(page);
  await page.mouse.move(box.x + box.width / 2 + 230, box.y + box.height / 2 - 180, {steps: 6});
  await page.mouse.up();
  const moved = (await page.locator('#aipraat-audio-button').boundingBox())!;
  expect(moved.x).toBeCloseTo(box.x + 230, 0);
  expect(moved.y).toBeCloseTo(box.y - 180, 0);
  expect(await page.evaluate(() => (window as any).events)).toEqual([]);
  await showCard(page);
  expect((await page.locator('#aipraat-audio-button').boundingBox())!.x).toBeCloseTo(moved.x, 0);
  expect((await page.locator('#aipraat-audio-button').boundingBox())!.y).toBeCloseTo(moved.y, 0);
  await page.locator('#aipraat-audio-button').click();
  expect(await page.evaluate(() => (window as any).events)).toEqual(['aipraat:open']);

  await holdButton(page);
  await page.mouse.move(1500, 1200);
  await page.mouse.up();
  const edge = (await page.locator('#aipraat-audio-button').boundingBox())!;
  expect(edge.x + edge.width).toBeLessThanOrEqual(900);
  expect(edge.y + edge.height).toBeLessThanOrEqual(650);
  await page.setViewportSize({width: 320, height: 240});
  const small = (await page.locator('#aipraat-audio-button').boundingBox())!;
  expect(small.x + small.width).toBeLessThanOrEqual(320);
  expect(small.y + small.height).toBeLessThanOrEqual(240);
  await holdButton(page);
  await page.mouse.move(-100, -100);
  await page.mouse.up();
  const origin = (await page.locator('#aipraat-audio-button').boundingBox())!;
  expect(origin.x).toBe(0);
  expect(origin.y).toBe(0);
  expect(await page.evaluate(() => (window as any).events)).toEqual(['aipraat:open']);
});

test('holding still and dragging immediately never open audio', async ({page}) => {
  const box = await holdButton(page);
  await page.mouse.up();
  expect(await page.evaluate(() => (window as any).events)).toEqual([]);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 30, box.y + box.height / 2);
  await page.mouse.up();
  expect((await page.locator('#aipraat-audio-button').boundingBox())!.x).toBeCloseTo(box.x + 30, 0);
  expect(await page.evaluate(() => (window as any).events)).toEqual([]);
  await page.locator('#aipraat-audio-button').focus();
  await page.keyboard.press('Enter');
  expect(await page.evaluate(() => (window as any).events)).toEqual(['aipraat:open']);
});

test('card changes, cancellation, and silent cards clear a pending press', async ({page}) => {
  const box = (await page.locator('#aipraat-audio-button').boundingBox())!;
  await page.mouse.move(box.x + 20, box.y + 10);
  await page.mouse.down();
  await showCard(page);
  await page.mouse.up();
  await page.waitForTimeout(400);
  await expect(page.locator('#aipraat-audio-button')).toHaveCSS('cursor', 'grab');
  await holdButton(page);
  await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  await page.mouse.up();
  await page.locator('#aipraat-audio-button').click();
  expect(await page.evaluate(() => (window as any).events)).toEqual(['aipraat:open']);
  await showCard(page, 'silent');
  await expect(page.locator('#aipraat-audio-button')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).aipraatDetachButton)).toBeNull();
});

test('both entry modes keep the magnifier and draggable button independent', async ({page}) => {
  await showCard(page, 'both');
  await showCard(page, 'both');
  await page.locator('.Search').click();
  await page.locator('#aipraat-audio-button').click();
  expect(await page.evaluate(() => (window as any).events)).toEqual(['aipraat:open', 'aipraat:open']);
  await showCard(page, 'magnifier');
  await expect(page.locator('#aipraat-audio-button')).toHaveCount(0);
  await page.locator('.Search').click();
  expect(await page.evaluate(() => (window as any).events)).toHaveLength(3);
  await showCard(page, 'silent');
  expect(await page.evaluate(() => (window as any).aipraatMagnifierHandler)).toBeNull();
});

test('a held press still starts dragging after the pointer moves before the delay', async ({page}) => {
  const button = page.locator('#aipraat-audio-button');
  const box = (await button.boundingBox())!;
  const start = {x: box.x + box.width / 2, y: box.y + box.height / 2};
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  // A person may start moving while the long-press delay is still running.
  await page.mouse.move(start.x + 24, start.y - 12, {steps: 4});
  await expect(button).toHaveCSS('cursor', 'grabbing');
  await page.mouse.move(start.x + 180, start.y - 90, {steps: 6});
  await page.mouse.up();
  const moved = (await button.boundingBox())!;
  expect(moved.x).toBeCloseTo(box.x + 180, 0);
  expect(moved.y).toBeCloseTo(box.y - 90, 0);
  expect(await page.evaluate(() => (window as any).events)).toEqual([]);
  await button.click();
  expect(await page.evaluate(() => (window as any).events)).toEqual(['aipraat:open']);
});

test('button tracks every mouse move without waiting for the long-press timer', async ({page}) => {
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  const button = page.locator('#aipraat-audio-button');
  const box = (await button.boundingBox())!;
  const start = {x: box.x + box.width / 2, y: box.y + box.height / 2};
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  // No timer can expire while the clock is paused.
  for (const [dx, dy] of [[120, -50], [220, -100], [60, 30], [-10, 0]]) {
    await page.mouse.move(start.x + dx, start.y + dy);
    const moved = (await button.boundingBox())!;
    expect(moved.x).toBeCloseTo(box.x + dx, 0);
    expect(moved.y).toBeCloseTo(box.y + dy, 0);
  }
  await page.mouse.up();
  expect(await page.evaluate(() => (window as any).events)).toEqual([]);
});
