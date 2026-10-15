/** DOM/worker protocol checks; no microphone, real network, or model download. */
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const original=fs.readFileSync(path.join(__dirname,'../../medlab/static/voice.js'),'utf8');
class Element {
  constructor() { this.hidden=false;this.disabled=false;this.value='';this.defaultValue='';this.checked=false;this.textContent='';this.children=[];this.dataset={};this.attributes={};this.listeners=new Map();this.maxLength=2000;this.classList={add(){},remove(){},toggle(){}}; }
  addEventListener(type,callback) { const list=this.listeners.get(type)||[];list.push(callback);this.listeners.set(type,list); }
  dispatchEvent(event) { for(const callback of this.listeners.get(event.type)||[]) callback(event);return true; }
  click() { if(!this.disabled) this.dispatchEvent({type:'click'}); }
  setAttribute(key,value) {this.attributes[key]=value;}
  removeAttribute(key) {delete this.attributes[key];}
  append(...nodes) {for(const node of nodes)node.parentElement=this;this.children.push(...nodes);if(!this.value&&nodes[0]?.value)this.value=nodes[0].value;}
  replaceChildren(...nodes) {this.children=[];this.value='';this.append(...nodes);}
  focus() {this.focused=true;}
  querySelector(selector) {for(const child of this.children){if(selector==='span'&&child.tagName==='SPAN'||selector.startsWith('.')&&child.className?.split(' ').includes(selector.slice(1))||selector==='[data-listen-turn]'&&child.dataset.listenTurn)return child;const found=child.querySelector?.(selector);if(found)return found;}return null;}
  remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(child=>child!==this);}
}
const defaults={provider:'local',available:true,supported:true,gender:'male',gender_locked:true,default_voice:'pm_alex',
  voices:[{id:'pf_dora',label:'Dora',gender:'female'},{id:'pm_alex',label:'Alex',gender:'male'},{id:'pm_santa',label:'Santa',gender:'male'}],
  models_asr:{download_mb:100},models_tts:{download_mb:120},max_recording_seconds:45};
async function harness(configuration={}, {workerLoads=true,micDenies=false,streamChunks=1,streamFinishes=true,withApp=false,micDeferred=false,messageFails=false,messageDeferred=false,asrFails=false,autoplayBlocked=false,bodyDeferred=false}={}) {
  const nodes=new Map(),workers=[],requests=[],audioContexts=[],streams=[],recorders=[];let micCalls=0,energy=0,now=0,nextTimer=0,resolveMic,resolveMessage; const timers=new Map();
  const schedule=(fn,delay,repeat=false)=>{const id=++nextTimer;timers.set(id,{fn,at:now+delay,delay,repeat});return id;};
  const advance=async(ms)=>{const end=now+ms;while(true){const entry=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!entry)break;const[id,t]=entry;now=t.at;if(t.repeat)t.at+=t.delay;else timers.delete(id);t.fn();await settle();}now=end;await settle();};
  const get=selector=>{if(!nodes.has(selector))nodes.set(selector,new Element());return nodes.get(selector);};
  get('#consultation-app').dataset.sessionId='local-session';get('meta[name=csrf-token]').content='csrf';get('#patient-data').textContent=JSON.stringify({name:'Paciente',initials:'P'});
  get('#chat-form').querySelector=selector=>get('#chat-form '+selector);
  get('#chat-form').requestSubmit=()=>get('#chat-form').dispatchEvent(new Event('submit',{cancelable:true}));
  for(const id of ['voice-review','voice-transcribing','voice-recording','voice-local-models','voice-player','voice-settings'])get(`#${id}`).hidden=true;
  get('#voice-studio .voice-mark svg').cloneNode=()=>new Element();
  class FakeWorker extends Element {
    constructor(url,options){super();this.url=url;this.options=options;this.messages=[];workers.push(this);}
    postMessage(data,transfer){this.messages.push({data,transfer});if(data.type==='load'&&workerLoads)queueMicrotask(()=>this.dispatchEvent({type:'message',data:{type:'ready',id:data.id,device:'wasm'}}));
      if(data.type==='transcribe')queueMicrotask(()=>this.dispatchEvent({type:'message',data:asrFails?{type:'error',id:data.id,message:'Transcrição falhou.'}:{type:'result',id:data.id,text:'Estou com dor há três dias.'}}));
      if(data.type==='synthesize')queueMicrotask(()=>{
        for(let index=0;index<streamChunks;index++)this.dispatchEvent({type:'message',data:{type:'chunk',id:data.id,index,total:streamChunks,samples:Float32Array.from({length:2400},()=>.1),sampleRate:24000}});
        if(streamFinishes)this.dispatchEvent({type:'message',data:{type:'result',id:data.id,streamed:true,samples:new Float32Array(0),sampleRate:24000}});
      });}
    terminate(){this.terminated=true;}
  }
  class Context extends Element {
    constructor(){super();this.state=autoplayBlocked?'suspended':'running';this.sources=[];audioContexts.push(this);}
    get currentTime(){return now/1000;}
    async resume(){if(autoplayBlocked)return new Promise(()=>{});this.state='running';}
    async close(){this.state='closed';}
    async decodeAudioData(){return{duration:1};}
    createMediaStreamSource(){return{connect(){},disconnect(){}};}
    createAnalyser(){return{fftSize:2048,getFloatTimeDomainData(samples){samples.fill(energy);},disconnect(){}};}
    createBuffer(channels,length,rate){return{duration:length/rate,copyToChannel(){}};}
    createBufferSource(){const source=new Element();source.connect=()=>{};source.start=time=>{source.startAt=time;};source.stop=()=>{source.stopped=true;};source.disconnect=()=>{};this.sources.push(source);return source;}
  }
  class Offline extends Context {async startRendering(){return{getChannelData:()=>Float32Array.from({length:16000},()=>.2)};}}
  class Recorder extends Element {static isTypeSupported(){return true;}constructor(){super();this.state='inactive';this.mimeType='audio/webm';recorders.push(this);}start(){this.state='recording';}stop(){this.state='inactive';this.dispatchEvent({type:'dataavailable',data:new Blob(['audio'])});queueMicrotask(()=>this.dispatchEvent({type:'stop'}));}}
  const navigator={mediaDevices:{getUserMedia:async()=>{micCalls++;if(micDenies)throw Object.assign(new Error('denied'),{name:'NotAllowedError'});const track=new Element();track.stopped=false;track.stop=()=>{track.stopped=true;};const stream={getTracks:()=>[track]};streams.push(stream);if(micDeferred)await new Promise(resolve=>{resolveMic=resolve;});return stream;}}};
  const document=Object.assign(new Element(),{hidden:false,body:new Element(),getElementById:id=>get(`#${id}`),querySelector:get,querySelectorAll:()=>[],createElement:tag=>Object.assign(new Element(),{tagName:tag.toUpperCase()})});
  const window=Object.assign(new Element(),{location:{href:'https://medlab.test/sessao/local-session',origin:'https://medlab.test'},isSecureContext:true,AudioContext:Context,OfflineAudioContext:Offline});
  const context={document,window,navigator,Worker:FakeWorker,WebAssembly,MediaRecorder:Recorder,Float32Array,ArrayBuffer,DataView,Blob,URL,AbortController,Event,CustomEvent:class extends Event{constructor(type,options={}){super(type);this.detail=options.detail;}},DOMException,console,Date:class extends Date{static now(){return now;}},setTimeout:(fn,ms)=>schedule(fn,ms),clearTimeout:id=>timers.delete(id),setInterval:(fn,ms)=>schedule(fn,ms,true),clearInterval:id=>timers.delete(id),
    localStorage:{getItem(){return null;},setItem(){}},sessionStorage:{getItem(){return null;},setItem(){}},Option:class extends Element{constructor(label,value){super();this.textContent=label;this.value=value;}},
    fetch:async(url,options={})=>{requests.push({url,options});if(url.endsWith('/mensagem')){if(messageDeferred)await new Promise(resolve=>{resolveMessage=resolve;});return{ok:!messageFails,json:async()=>{if(bodyDeferred){await new Promise(resolve=>{resolveMessage=resolve;});if(options.signal.aborted)throw new DOMException('aborted','AbortError');}return messageFails?{error:'Falha da consulta.'}:{answer:'Minha resposta salva.',turn_id:42,turn_count:1};}};}return{ok:true,json:async()=>url.endsWith('/config')?{...defaults,...configuration}:{text:'Estou com dor no peito há três dias.',voice_id:JSON.parse(options.body).voice_id}};}};
  if(withApp)vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../medlab/static/app.js'),'utf8'),context);
  vm.runInNewContext(original,context);await settle();
  return{get,workers,requests,audioContexts,streams,recorders,document,window,advance,energy:value=>{energy=value;},resolveMic:()=>resolveMic?.(),resolveMessage:()=>resolveMessage?.(),micCalls:()=>micCalls,app:get('#consultation-app')};
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

async function startContinuous(options={}) {
  const h=await harness({}, {withApp:true,...options});
  h.get('#voice-continuous-toggle').click();await settle();
  return h;
}
async function speak(h) {
  h.energy(0);await h.advance(300);
  h.energy(.08);await h.advance(650);
  h.energy(0);await h.advance(1100);
}
function answerBubble(h) {return h.get('#chat-thread').children.find(node=>node.className==='chat-message patient-message')?.querySelector('.message-bubble');}
function sends(h) {return h.requests.filter(request=>request.url.endsWith('/mensagem'));}

test('continuous VAD sends once, synchronizes text, and listens again only after the final audible chunk',async()=>{
  const h=await startContinuous({streamChunks:3});
  assert.equal(h.app.dataset.continuousState,'listening');assert.equal(h.micCalls(),1);
  assert.equal(h.audioContexts[0].state,'running');
  await speak(h);
  assert.equal(h.streams[0].getTracks()[0].stopped,true);
  assert.equal(sends(h).length,1);const sent=JSON.parse(sends(h)[0].options.body);
  assert.equal(sent.question,'Estou com dor há três dias.');assert.ok(sent.request_id);
  assert.equal(h.get('#voice-review').hidden,true);
  assert.equal(h.app.dataset.continuousState,'speaking');
  assert.equal(answerBubble(h).textContent,'Preparando voz do paciente…');
  assert.equal(h.micCalls(),1);await h.advance(40);
  assert.equal(answerBubble(h).textContent,'Minha resposta salva.');
  const sources=h.audioContexts[0].sources;assert.equal(sources.length,3);
  sources[0].dispatchEvent({type:'ended'});sources[1].dispatchEvent({type:'ended'});await h.advance(400);
  assert.equal(h.micCalls(),1);
  sources[2].dispatchEvent({type:'ended'});await h.advance(350);
  assert.equal(h.micCalls(),2);assert.equal(h.app.dataset.continuousState,'listening');
  assert.equal(h.audioContexts[0].state,'running');
  h.get('#voice-continuous-toggle').click();await settle();
  assert.equal(h.streams[1].getTracks()[0].stopped,true);assert.equal(h.audioContexts[0].state,'closed');
});

test('steady background noise is calibrated and no-speech timeout never reaches ASR or sends text',async()=>{
  const h=await startContinuous();h.energy(.02);await h.advance(15050);
  assert.equal(h.app.dataset.continuousState,'idle');assert.equal(sends(h).length,0);
  assert.equal(h.workers.flatMap(worker=>worker.messages).filter(item=>item.data.type==='transcribe').length,0);
  assert.equal(h.streams[0].getTracks()[0].stopped,true);
});

test('a short microphone click is insufficient speech and word pauses do not split an utterance',async()=>{
  const short=await startContinuous();await short.advance(300);short.energy(.09);await short.advance(150);short.energy(0);await short.advance(14600);
  assert.equal(sends(short).length,0);assert.equal(short.app.dataset.continuousState,'idle');
  const h=await startContinuous();await h.advance(300);h.energy(.08);await h.advance(400);h.energy(0);await h.advance(650);
  assert.equal(h.app.dataset.continuousState,'listening');assert.equal(sends(h).length,0);
  h.energy(.08);await h.advance(400);h.energy(0);await h.advance(1150);
  assert.equal(sends(h).length,1);h.get('#voice-continuous-toggle').click();
});

test('stop works during model preparation and ignores late model completion',async()=>{
  const h=await startContinuous({workerLoads:false});assert.equal(h.app.dataset.continuousState,'preparing');
  h.get('#voice-continuous-toggle').click();await settle();
  assert.equal(h.app.dataset.continuousState,'idle');assert.equal(h.micCalls(),0);
  for(const worker of h.workers){assert.equal(worker.terminated,true);worker.dispatchEvent({type:'message',data:{type:'ready',id:worker.messages[0].data.id}});}
  await settle();assert.equal(h.micCalls(),0);
});

test('stop during microphone permission releases a later granted stream without recording',async()=>{
  const h=await startContinuous({micDeferred:true});assert.equal(h.micCalls(),1);
  h.get('#voice-continuous-toggle').click();h.resolveMic();await settle();
  assert.equal(h.streams[0].getTracks()[0].stopped,true);assert.equal(h.recorders.length,0);assert.equal(h.app.dataset.continuousState,'idle');
});

test('stop during an in-flight automatic question aborts it and late success cannot speak or rearm',async()=>{
  const h=await startContinuous({messageDeferred:true});await speak(h);assert.equal(h.app.dataset.continuousState,'waiting');
  h.get('#voice-continuous-toggle').click();assert.equal(sends(h)[0].options.signal.aborted,true);
  h.resolveMessage();await settle();await h.advance(2000);
  assert.equal(h.app.dataset.continuousState,'idle');assert.equal(h.micCalls(),1);
  assert.equal(h.get('#question').value,'Estou com dor há três dias.');assert.equal(sends(h).length,1);
  assert.equal(h.requests.filter(request=>request.url.endsWith('/falar')).length,0);
});

test('API and recognition failures pause the loop without automatic retries',async()=>{
  const h=await startContinuous({messageFails:true});await speak(h);await h.advance(3000);
  assert.equal(h.app.dataset.continuousState,'idle');assert.equal(sends(h).length,1);assert.equal(h.get('#question').value,'Estou com dor há três dias.');
  assert.equal(h.micCalls(),1);assert.equal(h.get('#question').readOnly,false);
  const asr=await startContinuous({asrFails:true});await speak(asr);await asr.advance(3000);
  assert.equal(asr.app.dataset.continuousState,'idle');assert.equal(sends(asr).length,0);assert.equal(asr.micCalls(),1);
});

test('media recorder error, track ending and page visibility all stop listening',async()=>{
  for(const kind of ['recorder','track','visibility']){
    const h=await startContinuous();
    if(kind==='recorder')h.recorders[0].dispatchEvent({type:'error'});
    if(kind==='track')h.streams[0].getTracks()[0].dispatchEvent({type:'ended'});
    if(kind==='visibility'){h.document.hidden=true;h.document.dispatchEvent({type:'visibilitychange'});}
    await settle();await h.advance(2000);
    assert.equal(h.app.dataset.continuousState,'idle',kind);assert.equal(h.streams[0].getTracks()[0].stopped,true,kind);assert.equal(sends(h).length,0,kind);
  }
});

test('an autoplay block stops preparation before asking for a microphone',async()=>{
  const h=await startContinuous({autoplayBlocked:true});await h.advance(1500);
  assert.equal(h.app.dataset.continuousState,'idle');assert.equal(h.micCalls(),0);assert.equal(h.audioContexts[0].state,'closed');
});

test('stopping or failing TTS reveals the saved response immediately and never reopens the mic',async()=>{
  for(const kind of ['stop','error']){
    const h=await startContinuous({streamFinishes:false});await speak(h);
    assert.equal(answerBubble(h).textContent,'Preparando voz do paciente…');
    if(kind==='stop')h.get('#voice-continuous-toggle').click();
    else h.workers.find(worker=>worker.url.includes('kokoro')).dispatchEvent({type:'error',preventDefault(){}});
    await settle();await h.advance(2000);
    assert.equal(answerBubble(h).textContent,'Minha resposta salva.',kind);assert.equal(h.app.dataset.continuousState,'idle',kind);assert.equal(h.micCalls(),1,kind);
  }
});

test('suspended audio pauses the cycle and preserves a saved text response',async()=>{
  const h=await startContinuous();await speak(h);h.audioContexts[0].state='suspended';h.audioContexts[0].dispatchEvent({type:'statechange'});await settle();
  assert.equal(h.app.dataset.continuousState,'idle');assert.equal(answerBubble(h).textContent,'Minha resposta salva.');assert.equal(h.micCalls(),1);
});

test('stopping between recorder.stop and its delayed stop event discards the pending transcription',async()=>{
  const h=await startContinuous();await h.advance(300);h.energy(.08);await h.advance(650);
  h.get('#voice-finish-recording').click();
  h.get('#voice-continuous-toggle').click();
  await settle();await h.advance(2000);
  assert.equal(h.app.dataset.continuousState,'idle');assert.equal(sends(h).length,0);
  assert.equal(h.workers.flatMap(worker=>worker.messages).filter(item=>item.data.type==='transcribe').length,0);
  assert.equal(h.get('#voice-review').hidden,true);assert.equal(h.micCalls(),1);
});

test('an error from an old recorder cannot interrupt a restarted conversation',async()=>{
  const h=await startContinuous();const previous=h.recorders[0];
  h.get('#voice-continuous-toggle').click();await settle();
  h.get('#voice-continuous-toggle').click();await settle();
  assert.equal(h.micCalls(),2);assert.equal(h.app.dataset.continuousState,'listening');
  previous.dispatchEvent({type:'error'});await settle();
  assert.equal(h.app.dataset.continuousState,'listening');assert.equal(h.streams[1].getTracks()[0].stopped,false);
  h.get('#voice-continuous-toggle').click();
});

test('cancellation while reading the API body preserves the cancellation warning and question',async()=>{
  const h=await startContinuous({bodyDeferred:true});await speak(h);
  h.get('#voice-continuous-toggle').click();h.resolveMessage();await settle();
  assert.match(h.get('#chat-error').textContent,/servidor pode ter concluído/);
  assert.equal(h.get('#question').value,'Estou com dor há três dias.');assert.equal(h.app.dataset.continuousState,'idle');assert.equal(h.micCalls(),1);
});

test('last audio ended reveals text even if the first-chunk timer has not run',async()=>{
  const h=await startContinuous();await speak(h);
  assert.equal(answerBubble(h).textContent,'Preparando voz do paciente…');
  h.audioContexts[0].sources[0].dispatchEvent({type:'ended'});
  assert.equal(answerBubble(h).textContent,'Minha resposta salva.');
  h.get('#voice-continuous-toggle').click();await h.advance(1000);assert.equal(h.micCalls(),1);
});
