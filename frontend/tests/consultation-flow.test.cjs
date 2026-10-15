/** Learner-driven navigation only: no real microphone, network, or evaluation. */
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../medlab/static/app.js'), 'utf8');

class Element {
  constructor(id='') { this.id=id;this.dataset={};this.listeners=new Map();this.attributes={};this.children=[];this.hidden=false;this.value='';this.defaultValue='';this.textContent='';this.disabled=false;this.classList={toggle(){},add(){},remove(){}}; }
  addEventListener(type,callback) { const list=this.listeners.get(type)||[];list.push(callback);this.listeners.set(type,list); }
  dispatchEvent(event) { for(const callback of this.listeners.get(event.type)||[])callback(event);return true; }
  click() { this.dispatchEvent(new Event('click')); }
  setAttribute(key,value){this.attributes[key]=value;}
  removeAttribute(key){delete this.attributes[key];}
  focus(){this.focusCount=(this.focusCount||0)+1;}
  scrollIntoView(){this.scrolled=true;}
  append(...nodes){for(const node of nodes)node.parentElement=this;this.children.push(...nodes);}
  remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(child=>child!==this);}
  querySelector(selector){for(const child of this.children){if(selector.startsWith('.')&&child.className?.split(' ').includes(selector.slice(1)))return child;const found=child.querySelector(selector);if(found)return found;}return null;}
}
function harness(){
  const nodes=new Map(),requests=[],events=[];let completeResponse;
  const get=selector=>{if(!nodes.has(selector))nodes.set(selector,new Element(selector.startsWith('#')?selector.slice(1):''));return nodes.get(selector);};
  const tabs=['chat','exams','diagnosis'].map(name=>{const el=get(`#${name}-tab`);el.dataset.consultTab=name;return el;});
  const panels=['chat','exams','diagnosis'].map(name=>get(`#${name}-panel`));
  const finish=get('#conclude-consultation'),back=get('#return-to-chat');
  const selectors={'[data-consult-tab]':tabs,'.consult-panel':panels,'[data-conclude-consultation]':[finish],'[data-return-to-chat]':[back]};
  const document=Object.assign(new Element(),{body:new Element(),getElementById:id=>get(`#${id}`),querySelector:get,querySelectorAll:selector=>selectors[selector]||[],createElement:()=>new Element()});
  const window=Object.assign(new Element(),{location:{origin:'https://test.invalid'}});
  get('#consultation-app').dataset.sessionId='synthetic';
  get('#consultation-app').addEventListener('medlab:tab-change',event=>events.push(event.detail.name));
  get('#patient-data').textContent=JSON.stringify({name:'Paciente',initials:'P'});
  get('meta[name=csrf-token]').content='synthetic-csrf';
  get('#chat-form').querySelector=selector=>get('#chat-form '+selector);
  get('#chat-form').requestSubmit=()=>get('#chat-form').dispatchEvent(new Event('submit',{cancelable:true}));
  vm.runInNewContext(source,{document,window,Event,CustomEvent:class extends Event{constructor(type,options){super(type);this.detail=options.detail;}},AbortController,DOMException,URL,console,sessionStorage:{getItem(){return null;},setItem(){}},fetch:async(url,options)=>{requests.push({url,options});return await new Promise(resolve=>{completeResponse=()=>resolve({ok:true,json:async()=>({answer:'Resposta salva',turn_id:1,turn_count:1})});});}});
  return{get,finish,back,tabs,requests,events,complete:()=>completeResponse?.()};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('the explicit finish action opens conclusions and emits the voice stop contract without evaluating',()=>{
  const h=harness();
  assert.equal(h.get('#consultation-app').dataset.activeTab,'chat');
  h.finish.click();
  assert.equal(h.get('#chat-panel').hidden,true);
  assert.equal(h.get('#diagnosis-panel').hidden,false);
  assert.equal(h.get('#diagnosis-tab').attributes['aria-selected'],'true');
  assert.equal(h.get('#diagnosis').focusCount,1);
  assert.equal(h.get('#consultation-phase-label').textContent,'Conclusões antes da avaliação');
  assert.deepEqual(h.events,['chat','diagnosis']);
  assert.equal(h.requests.length,0);
  h.back.click();
  assert.equal(h.get('#chat-panel').hidden,false);
  assert.equal(h.get('#diagnosis-panel').hidden,true);
  assert.equal(h.get('#consultation-phase-label').textContent,'Consulta em andamento');
  assert.deepEqual(h.events,['chat','diagnosis','chat']);
  assert.equal(h.requests.length,0);
});

test('a pending answer is preserved before allowing the explicit conclusion action',async()=>{
  const h=harness();h.get('#question').value='Quando começou?';h.get('#chat-form').requestSubmit();
  assert.equal(h.requests.length,1);
  h.finish.click();
  assert.equal(h.get('#consultation-app').dataset.activeTab,'chat');
  assert.match(h.get('#conclusion-status').textContent,/Aguarde a resposta/);
  assert.equal(h.requests[0].options.signal.aborted,false);
  h.complete();await settle();await settle();
  h.finish.click();
  assert.equal(h.get('#consultation-app').dataset.activeTab,'diagnosis');
  assert.equal(h.get('#turn-count').textContent,1);
  assert.equal(h.get('#conclusion-status').hidden,true);
  assert.equal(h.requests.length,1);
  assert.ok(h.requests[0].url.endsWith('/mensagem'));
});

test('a late response does not steal focus from conclusions opened through the tabs',async()=>{
  const h=harness();h.get('#question').value='Como está se sentindo?';h.get('#chat-form').requestSubmit();
  h.tabs[2].click();h.get('#diagnosis').focus();
  h.complete();await settle();await settle();
  assert.equal(h.get('#consultation-app').dataset.activeTab,'diagnosis');
  assert.equal(h.get('#question').focusCount,undefined);
  assert.equal(h.get('#diagnosis-panel').hidden,false);
});
