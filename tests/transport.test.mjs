import test from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from '../web/store.mjs';
import { createTransport } from '../web/transport.js';

function stubRaf(t) {
  const raf = globalThis.requestAnimationFrame, caf = globalThis.cancelAnimationFrame;
  globalThis.cancelAnimationFrame = () => {};
  t.after(() => { globalThis.requestAnimationFrame = raf; globalThis.cancelAnimationFrame = caf; });
}

function fakePlayer() {
  const handlers = {};
  return {
    currentTime: 0, paused: true, calls: [],
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); },
    emit(type) { for (const fn of handlers[type] || []) fn(); },
    async play() { this.paused = false; this.calls.push('play'); this.emit('play'); },
    pause() { if (!this.paused) { this.paused = true; this.calls.push('pause'); this.emit('pause'); } },
  };
}

test('playRange seeks, plays once and pauses at the end ignoring loop', async t => {
  stubRaf(t);
  const frames = [];
  globalThis.requestAnimationFrame = fn => { frames.push(fn); return frames.length; };
  const player = fakePlayer();
  const store = createStore({ selection: { start: 0, end: 5 } });
  const transport = createTransport({ player, store, announce() {}, isLooping: () => true });
  await transport.playRange(10, 12);
  assert.equal(player.currentTime, 10);
  assert.equal(player.paused, false);
  player.currentTime = 12.1; // past the selection end with loop on: must not loop back
  player.emit('timeupdate');
  assert.equal(player.paused, true);
  assert.equal(player.currentTime, 12.1);
});

test('onFrame runs only while playing and can be unsubscribed', async t => {
  stubRaf(t);
  const queue = [];
  globalThis.requestAnimationFrame = fn => { queue.push(fn); return queue.length; };
  const player = fakePlayer();
  const transport = createTransport({ player, store: createStore(), announce() {} });
  const seen = [];
  const off = transport.onFrame(t => seen.push(t));
  await player.play();
  player.currentTime = 3;
  queue.shift()();
  assert.deepEqual(seen, [3]);
  off();
  queue.shift()?.();
  assert.deepEqual(seen, [3]);
});

test('a paused playRange does not leave loop and stop disabled', async t => {
  stubRaf(t);
  globalThis.requestAnimationFrame = () => 1;
  const player = fakePlayer();
  const store = createStore({ selection: { start: 2, end: 5 } });
  const transport = createTransport({ player, store, announce() {}, isLooping: () => true });
  await transport.playRange(10, 12);
  transport.toggle(); // user pauses
  assert.equal(player.paused, true);
  player.currentTime = 3;
  transport.toggle(); // plays again within the selection
  await Promise.resolve();
  player.currentTime = 5.2;
  player.emit('timeupdate');
  assert.equal(player.currentTime, 2);
});

test('completed block playback stays at its end on later paused timeupdates', async t => {
  stubRaf(t);
  globalThis.requestAnimationFrame = () => 1;
  const player = fakePlayer();
  const store = createStore({ selection: { start: 2, end: 5 } });
  const transport = createTransport({ player, store, announce() {}, isLooping: () => true });
  await transport.playRange(10, 12);
  player.currentTime = 12.1;
  player.emit('timeupdate');
  assert.equal(player.paused, true);
  player.emit('timeupdate'); // native audio can queue another event after pause
  assert.equal(player.currentTime, 12.1);
  transport.seek(20); // a paused seek outside the selected passage must stay there too
  player.emit('timeupdate');
  assert.equal(player.currentTime, 20);
});
