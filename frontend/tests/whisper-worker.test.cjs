/** Run with node --test frontend/tests/whisper-worker.test.cjs. No model download. */
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const original = fs.readFileSync(path.join(__dirname,'../whisper-worker.js'),'utf8');
function harness({gpu = false, gpuLoadFails = false, gpuInferenceFails = false, text = 'Estou com dor há três dias.'} = {}) {
  const messages = [], calls = [], inferences = [];
  let listener;
  const env = {backends:{onnx:{wasm:{}}}};
  const self = {caches:{}, navigator:gpu ? {gpu:{requestAdapter:async()=>({})}} : {},
    postMessage: data => messages.push(data), addEventListener: (_,callback) => { listener=callback; }};
  const pipeline = async (task,model,options) => {
    calls.push({task,model,options});
    options.progress_callback({status:'progress',file:'model.onnx',loaded:50,total:100,progress:50});
    if (options.device === 'webgpu' && gpuLoadFails) throw new Error('gpu load');
    const invoke = async (audio,settings) => {
      inferences.push({audio,settings,device:options.device});
      if (options.device === 'webgpu' && gpuInferenceFails) throw new Error('gpu inference');
      return {text};
    };
    invoke.dispose = async () => {};
    return invoke;
  };
  vm.runInNewContext(original.replace(/^import .*;$/m,'').replace(/^export /gm,''),{self,env,pipeline,Float32Array,console});
  return {messages,calls,inferences,env,send:async(data)=>{ listener({data}); await new Promise(r=>setImmediate(r)); }};
}
const speech = () => Float32Array.from({length:32000},(_,index)=>.2*Math.sin(index*.1));
test('only the explicit load request downloads model assets, with pinned multilingual weights', async () => {
  const h=harness(); assert.equal(h.calls.length,0);
  await h.send({id:'before',type:'transcribe',audio:speech(),sampleRate:16000});
  assert.equal(h.messages.at(-1).code,'NOT_READY'); assert.equal(h.calls.length,0);
  await h.send({id:'load',type:'load'});
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].model,'onnx-community/whisper-base');
  assert.equal(h.calls[0].options.revision,'1846881b6b3a3024392c1eea3ad983695bc23925');
  assert.equal(h.calls[0].options.dtype.encoder_model,'fp32');
  assert.equal(h.calls[0].options.dtype.decoder_model_merged,'q8');
  assert.equal(h.messages.at(-1).type,'ready');
  assert.equal(h.env.backends.onnx.wasm.numThreads,1);
});
test('transcribes local 16 kHz samples in Portuguese without translation',async()=>{
  const h=harness();await h.send({id:'load',type:'load'});
  await h.send({id:'speech',type:'transcribe',audio:speech(),sampleRate:16000});
  assert.equal(h.inferences[0].settings.language,'portuguese');
  assert.equal(h.inferences[0].settings.task,'transcribe');
  assert.equal(h.inferences[0].settings.chunk_length_s,30);
  assert.equal(h.messages.at(-1).text,'Estou com dor há três dias.');
});
test('WebGPU initialization failure falls back to WASM with the same weights',async()=>{
  const h=harness({gpu:true,gpuLoadFails:true});await h.send({id:'load',type:'load'});
  assert.deepEqual(h.calls.map(c=>c.options.device),['webgpu','wasm']);
  assert.equal(h.messages.at(-1).device,'wasm');
});
test('WebGPU inference failure retries the same local samples once in WASM',async()=>{
  const h=harness({gpu:true,gpuInferenceFails:true});await h.send({id:'load',type:'load'});
  const audio=speech();await h.send({id:'speech',type:'transcribe',audio,sampleRate:16000});
  assert.deepEqual(h.inferences.map(c=>c.device),['webgpu','wasm']);
  assert.equal(h.inferences[1].audio,audio);assert.equal(h.messages.at(-1).type,'result');
});
test('rejects silence, malformed samples, non-16kHz audio and recordings longer than 45s',async()=>{
  const h=harness();await h.send({id:'load',type:'load'});
  const cases=[{audio:new Float32Array(16000),sampleRate:16000,code:'NO_SPEECH'},
    {audio:speech(),sampleRate:48000,code:'INVALID_AUDIO'},
    {audio:new Float32Array(720001),sampleRate:16000,code:'INVALID_AUDIO'},
    {audio:[.1,.2],sampleRate:16000,code:'INVALID_AUDIO'},
    {audio:Float32Array.from({length:16000},()=>NaN),sampleRate:16000,code:'INVALID_AUDIO'}];
  for(const [index,entry] of cases.entries()) {await h.send({id:String(index),type:'transcribe',...entry});assert.equal(h.messages.at(-1).code,entry.code);}
  assert.equal(h.inferences.length,0);
});
test('empty or excessive transcripts never become a valid result',async()=>{
  for(const [text,code] of [['','NO_SPEECH'],['a'.repeat(2001),'TEXT_TOO_LONG']]) {
    const h=harness({text});await h.send({id:'load',type:'load'});
    await h.send({id:'speech',type:'transcribe',audio:speech(),sampleRate:16000});assert.equal(h.messages.at(-1).code,code);
  }
});
test('dispose closes the worker model and later inference cannot restart downloads',async()=>{
  const h=harness();await h.send({id:'load',type:'load'});await h.send({id:'dispose',type:'dispose'});
  assert.equal(h.messages.at(-1).type,'disposed');await h.send({id:'again',type:'load'});
  assert.equal(h.messages.at(-1).code,'DISPOSED');assert.equal(h.calls.length,1);
});
