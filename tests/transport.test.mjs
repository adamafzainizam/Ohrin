import test from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from '../web/store.mjs';
import { createTransport } from '../web/transport.js';

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

test('playRange seeks, plays once and pauses at the end ignoring loop', async () => {
  const frames = [];
  globalThis.requestAnimationFrame = fn => { frames.push(fn); return frames.length; };
  globalThis.cancelAnimationFrame = () => {};
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

test('onFrame runs only while playing and can be unsubscribed', async () => {
  const queue = [];
  globalThis.requestAnimationFrame = fn => { queue.push(fn); return queue.length; };
  globalThis.cancelAnimationFrame = () => {};
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
