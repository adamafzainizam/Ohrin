import test from 'node:test';
import assert from 'node:assert/strict';
import * as eq from '../web/eq.mjs';
test('EQ exposes pure contracts', () => assert.equal(typeof eq.defaultBands, 'function'));
test('flat chain, peak and Butterworth responses', () => {
  const bands = eq.defaultBands();
  for (const db of eq.curveDb(bands, [20, 100, 1000, 16000], 48000)) assert.ok(Math.abs(db) < 1e-8);
  const peak = {...bands[2], frequency_hz:1000, gain_db:6};
  assert.ok(Math.abs(eq.curveDb([peak], [1000],48000)[0]-6)<1e-8);
  for (const slope of [12,24]) {
    const pass = eq.clampBand({type:'lowpass',frequency_hz:1000,q:Math.SQRT1_2,slope_db_per_oct:slope,enabled:true});
    assert.ok(Math.abs(eq.curveDb([pass],[1000],48000)[0]+3.0103)<0.002);
  }
});
test('limits and mappings reject non-finite input', () => {
  assert.equal(eq.limitsFor('peaking',32000).frequency[1],14400);
  const b=eq.clampBand({type:'peaking',frequency_hz:Infinity,gain_db:100,q:'2',enabled:true});
  assert.equal(b.gain_db,12); assert.equal(b.q,1); assert.ok(Number.isFinite(b.frequency_hz));
  for(const f of [20,333,20000]) assert.ok(Math.abs(eq.xToFreq(eq.freqToX(f,48000),48000)-f)<1e-8);
});
test('canonical export/import is stable and untrusted fields are dropped', () => {
  const settings={bands:eq.defaultBands(),trim_db:1.234,level_match:true,preset:null};
  settings.bands[2].frequency_hz=333.333; settings.bands[2].q=1.234;
  const block=eq.eqNoteBlock(settings);
  const parsed=eq.parseEqFromNote({schema:'ohrin.passage-note',schema_version:3,eq:block});
  assert.equal(parsed.ok,true); assert.deepEqual(eq.eqNoteBlock(parsed.settings),block);
  assert.equal(eq.parseEqFromNote({schema:'ohrin.passage-note',schema_version:4}).ok,false);
  assert.equal(eq.parseEqFromNote({schema:'ohrin.passage-note',schema_version:2}).ok,false);
  assert.equal(eq.parseEqFromNote({schema:'ohrin.passage-note',schema_version:3,eq:{bands:[]}}).settings.bands.length,7);
});
test('presets use honest copy and compensation is bounded', () => {
  for(const p of eq.PRESETS) { assert.doesNotMatch(p.label+' '+p.description,/isolat|extract|separat|stem|detect|remov|confirm/i); assert.equal(p.bands.length,7); }
  assert.ok(Math.abs(eq.levelMatchDb(eq.defaultBands(),48000))<1e-8);
});
test('all filters have finite responses and disabled bands are identity',()=>{
 for(const type of eq.TYPES){const b=eq.clampBand({enabled:true,type,frequency_hz:1000,gain_db:6,q:1,slope_db_per_oct:24});assert.ok(eq.curveDb([b],[20,999,1001,19000]).every(Number.isFinite));assert.ok(eq.curveDb([{...b,enabled:false}],[20,1000,19000]).every(v=>Math.abs(v)<1e-8));}
 for(const type of ['lowshelf','highshelf'])assert.ok(Math.abs(eq.curveDb([eq.clampBand({enabled:true,type,frequency_hz:1000,gain_db:6})],[1000])[0]-3)<1e-8);
 assert.ok(eq.curveDb([eq.clampBand({enabled:true,type:'notch',frequency_hz:1000,q:4})],[1000])[0]<-40);
});
test('lowpass stopband slope and Web Audio resonance units',()=>{
 for(const slope of [12,24]){const b=eq.clampBand({enabled:true,type:'lowpass',frequency_hz:100,q:Math.SQRT1_2,slope_db_per_oct:slope});const db=eq.curveDb([b],[800,1600]);assert.ok(Math.abs(db[0]-db[1]-slope)<.3);}
 const b=eq.clampBand({enabled:true,type:'lowpass',frequency_hz:100,q:Math.SQRT1_2});assert.ok(Math.abs(eq.webAudioStages(b)[0].Q+3.0103)<.001);
});
test('hostile import is bounded, fresh and does not enable playback',()=>{
 const note=JSON.parse('{"schema":"ohrin.passage-note","schema_version":3,"eq":{"__proto__":{"polluted":true},"turned_on":true,"bands":[{"type":"evil","enabled":true,"gain_db":999,"q":-5,"frequency_hz":1}]}}');
 const parsed=eq.parseEqFromNote(note,32000);assert.equal(parsed.ok,true);assert.equal(parsed.settings.turned_on,false);assert.equal(parsed.settings.bands.length,7);assert.equal(parsed.settings.bands[0].frequency_hz,20);assert.equal('polluted' in parsed.settings,false);assert.equal('polluted' in parsed.settings.bands[0],false);assert.ok(parsed.notices.length);
 const huge={...note,eq:{bands:Array(1000).fill({enabled:true,type:'peaking',frequency_hz:50000})}};assert.equal(eq.parseEqFromNote(huge,32000).settings.bands[6].frequency_hz,14400);
});

test('soft clipper is transparent below the knee and never hard-clamps above 0 dBFS', () => {
  const curve = eq.shaperCurve();
  const at = x => eq.SHAPER_SPAN * curve[Math.round((x / eq.SHAPER_SPAN + 1) * 2048)]; // pre-gain, curve, post-gain
  for (const x of [0.1, 0.5, 0.7, -0.5]) assert.ok(Math.abs(at(x) - x) < 2e-3, `transparent at ${x}`);
  assert.ok(at(1.0) > at(0.9) && at(1.5) > at(1.0) && at(3.0) > at(1.5), 'still rising above 0 dBFS');
  assert.ok(at(3.9) < 1, 'bounded below full scale');
  assert.ok(Math.abs(at(-1.5) + at(1.5)) < 2e-3, 'odd symmetry');
});
test('import: a partial band keeps default enabled and rounding is not an adjustment', () => {
  const note = (bands, extra = {}) => ({ schema: 'ohrin.passage-note', schema_version: 3, eq: { bands, ...extra } });
  const partial = eq.parseEqFromNote(note([{ gain_db: 6 }]));
  assert.equal(partial.settings.bands[0].enabled, true);
  assert.ok(partial.notices.some(n => /missing values/.test(n)));
  assert.equal(eq.parseEqFromNote(note([{ enabled: false, type: 'peaking', frequency_hz: 60.4, gain_db: 0, q: 1, slope_db_per_oct: 12 }])).notices.length, 0);
  const clamped = eq.parseEqFromNote(note([{ enabled: true, type: 'peaking', frequency_hz: 1, gain_db: 0, q: 1, slope_db_per_oct: 12 }]));
  assert.ok(clamped.notices.some(n => /adjusted/.test(n)));
});
test('eqNoteBlock tolerates null and non-object settings', () => {
  for (const bad of [null, undefined, 5, 'x']) assert.equal(eq.eqNoteBlock(bad).bands.length, 7);
});
test('level match resolves a narrow high-Q boost', () => {
  const bands = eq.defaultBands();
  bands[3] = eq.clampBand({ enabled: true, type: 'peaking', frequency_hz: 1000, gain_db: 12, q: 18 }, 48000, 3);
  const match = eq.levelMatchDb(bands, 48000);
  assert.ok(match < 0 && match > -1.5, `a very narrow boost changes mean power only slightly, got ${match}`);
});
