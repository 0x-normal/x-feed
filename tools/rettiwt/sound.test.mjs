import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const html=readFileSync(new URL('../../x_engine/dashboard.html',import.meta.url),'utf8');
const source=html.split('<script>')[1].split("$('sound-toggle').onchange=")[0];
const elements=new Map(),preferences=new Map();
let tones=0,stops=0;
class AudioContext {
  state='running';currentTime=0;destination={};
  async resume(){this.state='running'}
  createOscillator(){return {frequency:{setValueAtTime(){}},connect(){},disconnect(){},
    start(){tones++},stop(){stops++}}}
  createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}}}
}
const runtime=vm.createContext({URL,URLSearchParams,
  window:{AudioContext,scrollY:0},
  document:{getElementById(id){if(!elements.has(id))elements.set(id,{});return elements.get(id)},querySelectorAll(){return []}},
  localStorage:{getItem(key){return preferences.get(key)||null}},
});
const run=code=>vm.runInContext(code,runtime);
run(source);
assert.equal(run('sound.enabled'),false);
preferences.set('x-feed-sound-enabled','true');
assert.equal(run('savedSound()'),true);
run('sound.enabled=true');
await run('activateSound()');
assert.equal(tones,0,'Unlocking audio must be silent');
run("observeActivity([{key:'old'}],{reset:true})");
assert.equal(tones,0,'Initial history must be silent');
run("observeActivity([{key:'new-a'},{key:'new-b'},{key:'old'}])");
assert.equal(tones,2,'One two-note chime for a batch, not per item');
run("observeActivity([{key:'new-a'},{key:'new-b'},{key:'old'}])");
assert.equal(tones,2,'Repeated polls must not repeat sound');
run("observeActivity([{key:'older'}],{more:true});observeActivity([{key:'older'}])");
assert.equal(tones,2,'Pagination must be silent');
run("sound.seen=null;observeActivity([{key:'different-filter'}],{reset:true})");
assert.equal(tones,2,'Changing filters must be silent');
run("sound.enabled=false;observeActivity([{key:'while-off'}])");
assert.equal(tones,2,'Muted feeds must be silent');
run("sound.enabled=true;observeActivity([{key:'while-off'}])");
assert.equal(tones,2,'Enabling sound must not replay muted history');
run("state.paused=true;observeActivity([{key:'paused'}]);state.paused=false");
assert.equal(tones,2,'An in-flight poll must respect pause');
run("sound.context.state='suspended';observeActivity([{key:'blocked'}])");
assert.equal(tones,2,'Do not queue browser-blocked sounds for later playback');
await run('activateSound(true)');
assert.equal(tones,4,'Test sound plays the chime');
run('stopSound()');
assert.ok(stops>0);

// The unseen-item banner keeps old cards on screen; fresh batches still chime
// only once while the user remains scrolled down.
runtime.fetch=async()=>({ok:true,json:async()=>({items:[{key:'banner-new'}],next:null})});
run("render=()=>{};state.items=[{key:'banner-old'}];sound.seen=new Set(['banner-old']);window.scrollY=300");
await run('loadFeed()');
assert.equal(tones,6);
assert.equal(elements.get('newbanner').hidden,false);
await run('loadFeed()');
assert.equal(tones,6);
let finish;
runtime.fetch=()=>new Promise(resolve=>{finish=resolve});
const pending=run('loadFeed()');
run('state.version++');
finish({ok:true,json:async()=>({items:[{key:'stale-response'}],next:null})});
await pending;
assert.equal(tones,6,'Discarded responses must not notify');
console.log('Sound preferences, new-activity detection, muting, and deduplication passed');
