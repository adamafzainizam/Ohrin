// Pure listening settings and Audio EQ Cookbook responses. No audio or DOM state.
export const TYPES = ['lowshelf','peaking','highshelf','highpass','lowpass','notch'];
export const clamp = (x,lo,hi) => Math.max(lo,Math.min(hi,x));
const finite = (x,fallback) => typeof x === 'number' && Number.isFinite(x) ? x : fallback;
export const isPass = type => type === 'highpass' || type === 'lowpass';
export const hasGain = type => ['peaking','lowshelf','highshelf'].includes(type);
export function limitsFor(type, sr=48000) {
  return {frequency:[20,Math.min(20000,0.45*sr)],gain:[-24,12],q:type==='peaking'?[0.2,18]:type==='notch'?[0.5,30]:isPass(type)?[0.5,4]:[1,1]};
}
export function defaultBands() {
  return [60,150,400,1000,2500,6000,12000].map((f,i)=>({enabled:true,type:i===0?'lowshelf':i===6?'highshelf':'peaking',frequency_hz:f,gain_db:0,q:1,slope_db_per_oct:12}));
}
// Sanitiser for every band that enters the app (editing, presets and imported notes). Anything missing or invalid
// falls back to the slot's default; an omitted `enabled` keeps the default, an explicit non-true value disables.
export function clampBand(raw={}, sr=48000, slot=0) {
  const d=defaultBands()[slot];
  raw=raw && typeof raw==='object'?raw:{};
  const type=TYPES.includes(raw.type)?raw.type:d.type;
  const lim=limitsFor(type,sr);
  const defaultQ=isPass(type)?Math.SQRT1_2:type==='notch'?4:1;
  return {
    enabled:raw.enabled===undefined?d.enabled:raw.enabled===true,
    type,
    frequency_hz:clamp(finite(raw.frequency_hz,d.frequency_hz),...lim.frequency),
    gain_db:hasGain(type)?clamp(finite(raw.gain_db,0),...lim.gain):0,
    q:clamp(finite(raw.q,defaultQ),...lim.q),
    slope_db_per_oct:isPass(type)&&raw.slope_db_per_oct===24?24:12,
  };
}
export const freqToX=(f,sr=48000)=>Math.log(f/20)/Math.log(limitsFor('peaking',sr).frequency[1]/20);
export const xToFreq=(x,sr=48000)=>20*(limitsFor('peaking',sr).frequency[1]/20)**clamp(x,0,1);
export const dbToY=db=>(24-db)/48;
export const yToDb=y=>clamp(24-y*48,-24,12);
const identity = () => ({type:'peaking',frequency:1000,gain:0,Q:1});
export function webAudioStages(b) {
  if(!b.enabled) return [identity(),identity()];
  const stage={type:b.type,frequency:b.frequency_hz,gain:b.gain_db,Q:isPass(b.type)?20*Math.log10(b.q):b.q};
  if(isPass(b.type)&&b.slope_db_per_oct===24) return [{...stage,Q:20*Math.log10(1/Math.sqrt(2+Math.SQRT2))},{...stage,Q:20*Math.log10((1/Math.sqrt(2-Math.SQRT2))*b.q/Math.SQRT1_2)}];
  return [stage,identity()];
}
export function cookbookMagnitude(s,f,sr) {
  const w=2*Math.PI*s.frequency/sr,c=Math.cos(w),sn=Math.sin(w),A=10**(s.gain/40);
  const q=isPass(s.type)?10**(s.Q/20):s.Q, alpha=sn/(2*q);
  let b0,b1,b2,a0,a1,a2;
  switch(s.type) {
    case 'peaking': b0=1+alpha*A;b1=-2*c;b2=1-alpha*A;a0=1+alpha/A;a1=-2*c;a2=1-alpha/A;break;
    case 'lowpass': b0=(1-c)/2;b1=1-c;b2=b0;a0=1+alpha;a1=-2*c;a2=1-alpha;break;
    case 'highpass': b0=(1+c)/2;b1=-(1+c);b2=b0;a0=1+alpha;a1=-2*c;a2=1-alpha;break;
    case 'notch': b0=1;b1=-2*c;b2=1;a0=1+alpha;a1=-2*c;a2=1-alpha;break;
    case 'lowshelf': { const t=Math.sqrt(2*A)*sn; b0=A*((A+1)-(A-1)*c+t);b1=2*A*((A-1)-(A+1)*c);b2=A*((A+1)-(A-1)*c-t);a0=(A+1)+(A-1)*c+t;a1=-2*((A-1)+(A+1)*c);a2=(A+1)+(A-1)*c-t;break; }
    case 'highshelf': { const t=Math.sqrt(2*A)*sn; b0=A*((A+1)+(A-1)*c+t);b1=-2*A*((A-1)+(A+1)*c);b2=A*((A+1)+(A-1)*c-t);a0=(A+1)-(A-1)*c+t;a1=2*((A-1)-(A+1)*c);a2=(A+1)-(A-1)*c-t;break; }
    default:return 1;
  }
  const v=2*Math.PI*f/sr;
  const power=(x,y,z)=>(x+y*Math.cos(v)+z*Math.cos(2*v))**2+(y*Math.sin(v)+z*Math.sin(2*v))**2;
  return Math.sqrt(Math.max(0,power(b0,b1,b2))/power(a0,a1,a2));
}
export function curveDb(bands,freqs,sr=48000,responder=cookbookMagnitude) {
  const stages=bands.flatMap(webAudioStages);
  return Array.from(freqs,f=>stages.reduce((db,s)=>db+20*Math.log10(Math.max(1e-15,responder(s,f,sr))),0));
}
export function levelMatchDb(bands,sr=48000) {
  // 512 log-spaced points (about 50 per octave) so narrow high-Q peaks and notches are not undersampled.
  const n=512;
  const freqs=Array.from({length:n},(_,i)=>20*(Math.min(16000,0.45*sr)/20)**(i/(n-1)));
  return clamp(-10*Math.log10(curveDb(bands,freqs,sr).reduce((sum,db)=>sum+10**(db/10),0)/n),-18,6);
}
export function soloParams(b) { return {frequency:b.frequency_hz,Q:['peaking','notch'].includes(b.type)?clamp(b.q,1,8):1}; }
export const isFlat=bands=>bands.every(b=>!b.enabled || (hasGain(b.type)&&b.gain_db===0));
export function describeBand(b,i) {return `Band ${i+1}, ${b.type}, ${Math.round(b.frequency_hz)} hertz, ${b.enabled?'on':'off'}${hasGain(b.type)?`, ${b.gain_db.toFixed(1)} decibels`:''}, Q ${b.q.toFixed(2)}`;}
export function changeBandType(b,type) { return {...b,type,q:isPass(type)?Math.SQRT1_2:type==='notch'?4:1,slope_db_per_oct:isPass(type)?24:12}; }
export function nudgeBand(b,key,shift=false,ctrl=false,sr=48000,slot=0) {
  let n={...b};
  if(key==='ArrowLeft'||key==='ArrowRight') n.frequency_hz*=2**((key==='ArrowRight'?1:-1)*(shift?1/3:1/24));
  if(key==='PageUp'||key==='PageDown'||ctrl&&['ArrowUp','ArrowDown'].includes(key)) n.q*=2**((['PageUp','ArrowUp'].includes(key)?1:-1)/4);
  else if(hasGain(n.type)&&['ArrowUp','ArrowDown'].includes(key)) n.gain_db+=(key==='ArrowUp'?1:-1)*(shift?3:0.5);
  if(key.toLowerCase()==='e') n.enabled=!n.enabled;
  if(key.toLowerCase()==='t') n=changeBandType(n,TYPES[(TYPES.indexOf(n.type)+1)%TYPES.length]);
  if(key==='Home') return defaultBands()[slot];
  return clampBand(n,sr,slot);
}
const preset=(id,label,description,entries)=>({id,label,description,bands:defaultBands().map((b,i)=>clampBand({enabled:true,type:'peaking',frequency_hz:b.frequency_hz,gain_db:0,q:1,...entries[i]}))});
export const PRESETS=[
  preset('low_focus','Low range lean','Turns down most content above about 700 Hz. Bass guitar, kick drum, low guitar notes and keys all live here.',[{type:'highpass',frequency_hz:30,q:Math.SQRT1_2,slope_db_per_oct:24},{frequency_hz:100,gain_db:3},{type:'lowpass',frequency_hz:700,q:Math.SQRT1_2,slope_db_per_oct:24}]),
  preset('mid_focus','Guitar range lean','Keeps roughly 120 Hz to 5 kHz, where guitars, voice and snare overlap.',[{type:'highpass',frequency_hz:120,q:Math.SQRT1_2,slope_db_per_oct:24},{frequency_hz:1500,gain_db:2,q:0.8},{type:'lowpass',frequency_hz:5000,q:Math.SQRT1_2}]),
  preset('attack_focus','Attack and pick lean','Brings forward pick, string and drum attack. Everything bright gets louder, not one part.',[{type:'lowshelf',frequency_hz:200,gain_db:-6},{frequency_hz:3500,gain_db:4,q:1.2},{type:'highshelf',frequency_hz:8000,gain_db:2}]),
  preset('narrow_mid','Narrow mid band','A telephone-like band to hear rhythm and mid-range movement without low end or air.',[{type:'highpass',frequency_hz:400,q:Math.SQRT1_2,slope_db_per_oct:24},{type:'lowpass',frequency_hz:3000,q:Math.SQRT1_2,slope_db_per_oct:24}]),
];
const round=(x,n)=>Number(x.toFixed(n));
function canonicalBands(bands,sr=48000) { return defaultBands().map((d,i)=>{const b=clampBand(bands?.[i]??d,sr,i);return {...b,frequency_hz:round(b.frequency_hz,0),gain_db:round(b.gain_db,1),q:round(b.q,2)};}); }
export function eqNoteBlock(settings) {
  settings=settings&&typeof settings==='object'?settings:{};
  const bands=canonicalBands(settings.bands);
  const p=PRESETS.find(p=>p.id===settings.preset);
  return {about:'Listening EQ settings you chose. Playback only; not analysis.',turned_on:settings.turned_on===true,bypassed:settings.bypassed===true,level_match:settings.level_match!==false,trim_db:round(clamp(finite(settings.trim_db,0),-12,6),1),preset:p&&JSON.stringify(canonicalBands(p.bands))===JSON.stringify(bands)?p.id:null,bands};
}
export function parseEqFromNote(obj,sr=48000) {
  if(!obj||typeof obj!=='object'||Array.isArray(obj)||obj.schema!=='ohrin.passage-note') return {ok:false,reason:'This is not an Ohrin note.'};
  if(!Number.isInteger(obj.schema_version)||obj.schema_version<1||obj.schema_version>3) return {ok:false,reason:obj.schema_version>3?'Made by a newer Ohrin; EQ not loaded.':'Unrecognised note version.'};
  if(obj.schema_version<3) return {ok:false,reason:'This note was saved before EQ existed; nothing loaded.'};
  if(!obj.eq||typeof obj.eq!=='object'||Array.isArray(obj.eq)) return {ok:false,reason:'No EQ settings in this note.'};
  const e=obj.eq;
  const rawBands=Array.isArray(e.bands)?e.bands:[];
  const bands=canonicalBands(rawBands,sr);
  const settings=eqNoteBlock({bands,trim_db:e.trim_db,level_match:e.level_match,preset:e.preset});
  // Rounding to the stored precision is not an adjustment; clamping or replacing a value is.
  const tolerance={frequency_hz:0.5,gain_db:0.05,q:0.005};
  let adjusted=0;
  let missing=0;
  bands.forEach((b,i)=>{
    const raw=rawBands[i];
    if(!raw||typeof raw!=='object') return;
    for(const k of Object.keys(b)) {
      if(raw[k]===undefined) { missing++; continue; }
      const same=k in tolerance&&typeof raw[k]==='number'?Math.abs(raw[k]-b[k])<=tolerance[k]:raw[k]===b[k];
      if(!same) adjusted++;
    }
  });
  if(e.trim_db!==undefined&&Math.abs(finite(e.trim_db,NaN)-settings.trim_db)>0.05) adjusted++;
  const notices=[];
  if(adjusted) notices.push(`${adjusted} values were adjusted to supported settings.`);
  if(missing) notices.push(`${missing} missing values were set to defaults.`);
  return {ok:true,settings,notices};
}

// Output soft clipper. Transparent below SOFT_KNEE (about -3 dBFS), a tanh knee above it. The Web Audio shaper only
// sees [-1, 1], so the graph feeds it the signal divided by SHAPER_SPAN and restores the level afterwards: the
// audible result is softClip(x) up to +12 dBFS rather than a hard clamp at 0 dBFS.
export const SOFT_KNEE = 0.708;
export const SOFT_RANGE = 0.269;
export const SHAPER_SPAN = 4;
export function softClip(x) {
  const a=Math.abs(x);
  return Math.sign(x)*(a<=SOFT_KNEE?a:SOFT_KNEE+SOFT_RANGE*Math.tanh((a-SOFT_KNEE)/SOFT_RANGE));
}
export function shaperCurve() {
  return Float32Array.from({length:4097},(_,i)=>softClip((i/2048-1)*SHAPER_SPAN)/SHAPER_SPAN);
}
