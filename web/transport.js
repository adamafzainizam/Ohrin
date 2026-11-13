import { shouldLoop, shouldStop } from './state.mjs';

// Play/pause, the play-generation token, speed and loop/stop for the audio element.
// `resume` readies the EQ audio context before play; `isLooping` reads the loop control.
export function createTransport({ player, store, announce, resume = async () => {}, isLooping = () => false }) {
  let playGeneration = 0;
  let playPending = false;
  let selectionPlaying = false; // true only while play()/toggle started selection playback
  let rangeEnd = null; // set by playRange: stop here once, ignoring loop
  let rafId = null;
  const frameFns = [];

  function emitFrame() { for (const fn of [...frameFns]) fn(player.currentTime); }
  function tick() {
    rafId = null;
    if (player.paused) return;
    checkRange();
    emitFrame();
    if (!player.paused && rafId === null) rafId = requestAnimationFrame(tick);
  }
  function startLoop() { if (rafId === null) rafId = requestAnimationFrame(tick); }
  function stopLoop() { if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; } }
  function checkRange() {
    if (rangeEnd !== null && player.currentTime >= rangeEnd) { rangeEnd = null; selectionPlaying = false; playGeneration++; player.pause(); }
  }

  async function startPlayback() {
    const token = ++playGeneration; playPending = true;
    try { await resume(); if (token !== playGeneration) return; await player.play(); }
    catch (error) { if (token === playGeneration) announce(error.message); }
    finally { if (token === playGeneration) playPending = false; }
  }
  function play() { rangeEnd = null; selectionPlaying = true; return startPlayback(); }
  function pause() { playGeneration++; playPending = false; rangeEnd = null; selectionPlaying = false; player.pause(); }
  // Drop any in-flight play request without pausing (used when a new file loads).
  function cancel() { playGeneration++; playPending = false; rangeEnd = null; selectionPlaying = false; }
  async function playRange(start_s, end_s) {
    player.currentTime = start_s;
    rangeEnd = end_s; selectionPlaying = false;
    await startPlayback();
  }
  function toggle() {
    const { selection } = store.get();
    if (playPending) { pause(); return; }
    if (player.paused) {
      if (player.currentTime >= selection.end || player.currentTime < selection.start) player.currentTime = selection.start;
      play();
    } else pause();
  }
  function onFrame(fn) {
    frameFns.push(fn);
    return () => { const i = frameFns.indexOf(fn); if (i >= 0) frameFns.splice(i, 1); };
  }
  function seek(t) { rangeEnd = null; selectionPlaying = false; player.currentTime = t; }

  player.addEventListener('play', () => startLoop());
  player.addEventListener('pause', stopLoop);
  // A selection that ends at the track end never reaches a timeupdate past its end: the element
  // stops at EOF, already paused. Handle that natural end here. A completed block range does not loop.
  player.addEventListener('ended', () => {
    stopLoop();
    const loopIt = selectionPlaying && rangeEnd === null && isLooping();
    rangeEnd = null; selectionPlaying = false;
    if (!loopIt) return;
    const { selection } = store.get();
    if (!shouldLoop(player.currentTime, selection, true)) return;
    player.currentTime = selection.start;
    play();
  });
  player.addEventListener('timeupdate', () => {
    if (rangeEnd !== null) { checkRange(); if (player.paused) emitFrame(); return; }
    if (player.paused) { emitFrame(); return; }
    const { selection } = store.get();
    const loop = isLooping();
    if (shouldLoop(player.currentTime, selection, loop)) player.currentTime = selection.start;
    else if (shouldStop(player.currentTime, selection, loop)) { player.pause(); player.currentTime = selection.end; }
    if (player.paused) emitFrame();
  });

  // DOM wiring for the transport controls; separate so the core runs without a DOM.
  function bindControls({ playButton, speed }) {
    speed.addEventListener('change', () => { player.playbackRate = Number(speed.value); player.preservesPitch = true; });
    playButton.addEventListener('click', toggle);
    player.addEventListener('play', () => { playButton.textContent = 'Ⅱ'; });
    player.addEventListener('pause', () => { playButton.textContent = '▶'; });
  }

  return { play, pause, playRange, onFrame, toggle, cancel, seek, bindControls };
}
