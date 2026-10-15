/** DOM/worker protocol checks; no microphone, real network, or model download. */
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const original=fs.readFileSync(path.join(__dirname,'../../medlab/static/voice.js'),'utf8');
class Element {
  constructor() { this.hidden=false;this.disabled=false;this.value='';this.checked=false;this.textContent='';this.children=[];this.dataset={};this.attributes={};this.listeners=new Map();this.maxLength=2000;this.classList={add(){},remove(){},toggle(){}}; }
  addEventListener(type,callback) { const list=this.listeners.get(type)||[];list.push(callback);this.listeners.set(type,list); }
  dispatchEvent(event) { for(const callback of this.listeners.get(event.type)||[]) callback(event);return true; }
  click() { if(!this.disabled) this.dispatchEvent({type:'click'}); }
  setAttribute(key,value) {this.attributes[key]=value;}
  removeAttribute(key) {delete this.attributes[key];}
  append(...nodes) {this.children.push(...nodes);if(!this.value&&nodes[0]?.value)this.value=nodes[0].value;}
  replaceChildren(...nodes) {this.children=[];this.value='';this.append(...nodes);}
  focus() {this.focused=true;}
  querySelector(selector) {return selector==='span'?this.children.find(child=>child.tagName==='SPAN')||null:null;}
}
const defaults={provider:'local',available:true,supported:true,gender:'male',gender_locked:true,default_voice:'pm_alex',
  voices:[{id:'pf_dora',label:'Dora',gender:'female'},{id:'pm_alex',label:'Alex',gender:'male'},{id:'pm_santa',label:'Santa',gender:'male'}],
  models_asr:{download_mb:100},models_tts:{download_mb:120},max_recording_seconds:45};
async function harness(configuration={}, {workerLoads=true,micDenies=false,streamChunks=1,streamFinishes=true}={}) {
  const nodes=new Map(),workers=[],requests=[],audioContexts=[];let micCalls=0;
  const get=selector=>{if(!nodes.has(selector))nodes.set(selector,new Element());return nodes.get(selector);};
  get('#consultation-app').dataset.sessionId='local-session';get('meta[name=csrf-token]').content='csrf';
  for(const id of ['voice-review','voice-transcribing','voice-recording','voice-local-models','voice-player','voice-settings'])get(`#${id}`).hidden=true;
  get('#voice-studio .voice-mark svg').cloneNode=()=>new Element();
  class FakeWorker extends Element {
    constructor(url,options){super();this.url=url;this.options=options;this.messages=[];workers.push(this);}
    postMessage(data,transfer){this.messages.push({data,transfer});if(data.type==='load'&&workerLoads)queueMicrotask(()=>this.dispatchEvent({type:'message',data:{type:'ready',id:data.id,device:'wasm'}}));
      if(data.type==='transcribe')queueMicrotask(()=>this.dispatchEvent({type:'message',data:{type:'result',id:data.id,text:'Estou com dor há três dias.'}}));
      if(data.type==='synthesize')queueMicrotask(()=>{
        for(let index=0;index<streamChunks;index++)this.dispatchEvent({type:'message',data:{type:'chunk',id:data.id,index,total:streamChunks,samples:Float32Array.from({length:2400},()=>.1),sampleRate:24000}});
        if(streamFinishes)this.dispatchEvent({type:'message',data:{type:'result',id:data.id,streamed:true,samples:new Float32Array(0),sampleRate:24000}});
      });}
    terminate(){this.terminated=true;}
  }
  class Context {
    constructor(){this.state='running';this.currentTime=0;this.sources=[];audioContexts.push(this);}
    async resume(){this.state='running';}
    async close(){this.state='closed';}
    async decodeAudioData(){return{duration:1};}
    createBuffer(channels,length,rate){return{duration:length/rate,copyToChannel(){}};}
    createBufferSource(){const source=new Element();source.connect=()=>{};source.start=time=>{source.startAt=time;};source.stop=()=>{source.stopped=true;};source.disconnect=()=>{};this.sources.push(source);return source;}
  }
  class Offline extends Context {async startRendering(){return{getChannelData:()=>Float32Array.from({length:16000},()=>.2)};}}
  class Recorder extends Element {static isTypeSupported(){return true;}constructor(){super();this.state='inactive';this.mimeType='audio/webm';}start(){this.state='recording';}stop(){this.state='inactive';this.dispatchEvent({type:'dataavailable',data:new Blob(['audio'])});queueMicrotask(()=>this.dispatchEvent({type:'stop'}));}}
  const navigator={mediaDevices:{getUserMedia:async()=>{micCalls++;if(micDenies)throw Object.assign(new Error('denied'),{name:'NotAllowedError'});return{getTracks:()=>[{stop(){}}]};}}};
  const document={getElementById:id=>get(`#${id}`),querySelector:get,querySelectorAll:()=>[],createElement:tag=>Object.assign(new Element(),{tagName:tag.toUpperCase()})};
  const window=Object.assign(new Element(),{location:{href:'https://medlab.test/sessao/local-session',origin:'https://medlab.test'},isSecureContext:true,AudioContext:Context,OfflineAudioContext:Offline});
  const context={document,window,navigator,Worker:FakeWorker,WebAssembly,MediaRecorder:Recorder,Float32Array,ArrayBuffer,DataView,Blob,URL,AbortController,Event,console,setTimeout,clearTimeout,setInterval,clearInterval,
    localStorage:{getItem(){return null;},setItem(){}},Option:class extends Element{constructor(label,value){super();this.textContent=label;this.value=value;}},
    fetch:async(url,options={})=>{requests.push({url,options});return{ok:true,json:async()=>url.endsWith('/config')?{...defaults,...configuration}:{text:'Estou com dor no peito há três dias.',voice_id:JSON.parse(options.body).voice_id}};}};
  vm.runInNewContext(original,context);await settle();
  return{get,workers,requests,audioContexts,micCalls:()=>micCalls,app:get('#consultation-app')};
}
async function settle(){await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));}
test('page load asks for neither microphone nor model download',async()=>{
  const h=await harness();assert.equal(h.workers.length,0);assert.equal(h.micCalls(),0);assert.equal(h.requests.length,1);
  assert.equal(h.get('#voice-local-models').hidden,false);
  assert.match(h.get('#asr-model-description').textContent,/100 MB/);assert.match(h.get('#tts-model-description').textContent,/120 MB/);
  h.get('#voice-record-button').click();await settle();assert.equal(h.workers.length,0);assert.equal(h.micCalls(),0);
  assert.match(h.get('#voice-status').textContent,/Ative a transcrição/);
});
test('each model activation is explicit, and cancelling a download terminates its worker',async()=>{
  const h=await harness({}, {workerLoads:false});h.get('#asr-model-activate').click();await settle();
  assert.equal(h.workers.length,1);assert.equal(h.workers[0].options.type,'module');assert.match(h.workers[0].url,/whisper-worker/);
  assert.equal(h.get('#asr-model-cancel').hidden,false);assert.equal(h.get('#tts-model-activate').disabled,false);
  h.get('#asr-model-cancel').click();assert.equal(h.workers[0].terminated,true);assert.equal(h.get('#asr-model-activate').disabled,false);
});
test('unavailable runtime explains setup without microphone or worker side effects',async()=>{
  const h=await harness({supported:false,reason:'A equipe precisa preparar os modelos.'});h.get('#voice-record-button').click();await settle();
  assert.equal(h.workers.length,0);assert.equal(h.micCalls(),0);assert.equal(h.get('#voice-local-models').hidden,true);assert.match(h.get('#voice-status').textContent,/preparar os modelos/);
});
test('voice list honors case gender and unknown gender requires an explicit choice',async()=>{
  const male=await harness();assert.deepEqual(male.get('#voice-variant').children.map(x=>x.value),['pm_alex','pm_santa']);assert.equal(male.get('#voice-gender-field').hidden,true);
  const unknown=await harness({gender:'unknown',gender_locked:false});assert.equal(unknown.get('#voice-gender-field').hidden,false);assert.equal(unknown.get('#voice-variant').disabled,true);
  unknown.get('#voice-gender').value='female';unknown.get('#voice-gender').dispatchEvent({type:'change'});assert.deepEqual(unknown.get('#voice-variant').children.map(x=>x.value),['pf_dora']);
});
test('permission denied restores the composer and never sends audio',async()=>{
  const h=await harness({}, {micDenies:true});h.get('#asr-model-activate').click();await settle();h.get('#voice-record-button').click();await settle();
  assert.equal(h.micCalls(),1);assert.equal(h.app.dataset.voiceBusy,'false');assert.equal(h.get('#question').readOnly,false);assert.match(h.get('#voice-status').textContent,/não foi permitido/);assert.equal(h.requests.length,1);
});
test('recording is transcribed locally and requires review plus a separate text send',async()=>{
  const h=await harness();h.get('#asr-model-activate').click();await settle();h.get('#voice-record-button').click();await settle();h.get('#voice-finish-recording').click();await settle();
  assert.equal(h.get('#voice-review').hidden,false);assert.equal(h.get('#voice-transcript').value,'Estou com dor há três dias.');
  assert.equal(h.get('#question').value,'');assert.equal(h.requests.length,1);
  const inference=h.workers[0].messages.find(item=>item.data.type==='transcribe');assert.equal(inference.data.sampleRate,16000);assert.ok(inference.data.audio instanceof Float32Array);assert.equal(inference.transfer[0],inference.data.audio.buffer);
  h.get('#voice-use-transcript').click();assert.equal(h.get('#question').value,'Estou com dor há três dias.');assert.equal(h.get('#voice-review').hidden,true);assert.equal(h.requests.length,1);
});
test('new responses do not auto-download the TTS model',async()=>{
  const h=await harness();h.get('#voice-autoplay').checked=true;
  const node=new Element(),parent=new Element();node.querySelector=selector=>selector==='[data-listen-turn]'?null:{parentElement:parent};
  h.app.dispatchEvent({type:'medlab:turn-saved',detail:{turnId:42,element:node}});await settle();assert.equal(h.workers.length,0);assert.equal(h.requests.length,1);
});
test('TTS consumes saved-turn JSON and streams local audio in order',async()=>{
  const h=await harness();h.get('#tts-model-activate').click();await settle();
  const node=new Element(),parent=new Element();node.querySelector=selector=>selector==='[data-listen-turn]'?null:{parentElement:parent};
  h.app.dispatchEvent({type:'medlab:turn-saved',detail:{turnId:42,element:node}});parent.children[0].click();await settle();
  assert.equal(h.requests.length,2);assert.ok(h.requests[1].url.endsWith('/voz/falar'));assert.deepEqual(JSON.parse(h.requests[1].options.body),{turn_id:42,voice_id:'pm_alex'});
  const synth=h.workers[0].messages.find(item=>item.data.type==='synthesize');assert.equal(synth.data.stream,true);assert.equal(synth.data.text,'Estou com dor no peito há três dias.');
  assert.equal(h.get('#voice-player').hidden,false);assert.match(h.get('#voice-player-label').textContent,/Ouvindo o paciente/);h.get('#voice-stop-playback').click();assert.equal(h.get('#voice-player').hidden,true);
});

test('streamed fragments are scheduled consecutively and the player ends only after the last source',async()=>{
  const h=await harness({}, {streamChunks:3});h.get('#tts-model-activate').click();await settle();
  const node=new Element(),parent=new Element();node.querySelector=selector=>selector==='[data-listen-turn]'?null:{parentElement:parent};
  h.app.dispatchEvent({type:'medlab:turn-saved',detail:{turnId:42,element:node}});parent.children[0].click();await settle();
  const sources=h.audioContexts[0].sources;assert.equal(sources.length,3);
  assert.ok(Math.abs(sources[1].startAt-sources[0].startAt-.1)<1e-10);assert.ok(Math.abs(sources[2].startAt-sources[1].startAt-.1)<1e-10);
  sources[0].dispatchEvent({type:'ended'});assert.equal(h.get('#voice-player').hidden,false);
  sources[1].dispatchEvent({type:'ended'});assert.equal(h.get('#voice-player').hidden,false);
  sources[2].dispatchEvent({type:'ended'});assert.equal(h.get('#voice-player').hidden,true);
  assert.equal(h.get('#tts-model-activate').disabled,true);assert.equal(h.audioContexts[0].state,'closed');
});
test('stopping unfinished synthesis terminates compute and suppresses late audio chunks',async()=>{
  const h=await harness({}, {streamFinishes:false});h.get('#tts-model-activate').click();await settle();
  const node=new Element(),parent=new Element();node.querySelector=selector=>selector==='[data-listen-turn]'?null:{parentElement:parent};
  h.app.dispatchEvent({type:'medlab:turn-saved',detail:{turnId:42,element:node}});parent.children[0].click();await settle();
  const context=h.audioContexts[0];context.sources[0].dispatchEvent({type:'ended'});assert.match(h.get('#voice-player-label').textContent,/próximo trecho/);
  h.get('#voice-stop-playback').click();await settle();assert.equal(h.workers[0].terminated,true);assert.equal(h.get('#voice-player').hidden,true);
  assert.equal(h.get('#tts-model-activate').disabled,false);assert.equal(context.state,'closed');
  const request=h.workers[0].messages.find(item=>item.data.type==='synthesize').data;
  h.workers[0].dispatchEvent({type:'message',data:{type:'chunk',id:request.id,index:1,total:2,samples:new Float32Array([.1]),sampleRate:24000}});
  assert.equal(context.sources.length,1);
});

test('a delayed error from a cancelled worker cannot stop a newly activated model',async()=>{
  const h=await harness({}, {workerLoads:false});h.get('#asr-model-activate').click();await settle();
  h.get('#asr-model-cancel').click();h.get('#asr-model-activate').click();await settle();
  assert.equal(h.workers.length,2);h.workers[0].dispatchEvent({type:'error',preventDefault(){}});
  assert.equal(h.workers[1].terminated,undefined);assert.equal(h.get('#asr-model-cancel').hidden,false);
  h.get('#asr-model-cancel').click();
});
