/** Complementary feedback UI: no provider, real network, or student records. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../../medlab/static/learning-review.js'),'utf8');

class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.dataset={};this.children=[];this.attributes={};this.listeners={};this.hidden=false;this.disabled=false;this._text=''; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { throw new Error('Untrusted feedback must never enter innerHTML'); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text='';this.children=[...nodes]; }
  setAttribute(name,value) { this.attributes[name]=value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name,fn) { (this.listeners[name] ||= []).push(fn); }
  click() { if (!this.disabled) for(const fn of this.listeners.click||[])fn(); }
}
function review() {
  const guidance={criterionIds:['criterion-1'],criteria:['Critério de referência'],actionIds:['D1'],evidence:[{id:'D1',text:'Minha hipótese registrada'}],why:'Compare o achado com a hipótese.',nextTime:'Explique a relação entre o achado e a hipótese.'};
  return {version:1,status:'educational',summary:'Orientações específicas para esta consulta.',
    interview:[{criterionId:'interview-1',criterion:'Investigar a duração',status:'missed',actionIds:[],evidence:[],why:'A duração ajuda a organizar a evolução.',nextTime:'Pergunte há quanto tempo ocorre.',exampleQuestion:'Quando isso começou?'}],
    diagnosis:structuredClone(guidance),plan:{...structuredClone(guidance),why:'A conduta precisa de acompanhamento.',nextTime:'Descreva como reavaliar.'},
    exams:[{examId:'physical-1',name:'Sinais vitais',type:'physical_exam',requested:false,required:true,judgement:'consider',why:'O resultado disponível pode contribuir.',nextTime:'Solicite e relacione à hipótese.'},{examId:'auxiliary-1',name:'Exame sintético',type:'auxiliary_exam',requested:true,required:false,judgement:'unnecessary',why:'A referência não sustenta a indicação.',nextTime:'Explique a indicação antes de solicitar.'}]};
}
function harness({saved=null,demo=false,present=true}={}) {
  const nodes=new Map(),created=[],requests=[];
  for(const id of ['learning-review','learning-review-content','learning-review-status','learning-review-error','learning-review-error-text','learning-review-retry','learning-review-data','saved-score'])nodes.set(id,new Element());
  nodes.get('saved-score').textContent='5/100';
  nodes.get('learning-review').dataset={sessionId:'synthetic-session',demoMode:String(demo)};
  nodes.get('learning-review-data').textContent=JSON.stringify(saved);
  nodes.get('learning-review-error').hidden=true;nodes.get('learning-review-content').hidden=true;
  const document={getElementById:id=>id==='learning-review'&&!present?null:nodes.get(id)||null,
    querySelector:selector=>selector==='meta[name=csrf-token]'?{content:'synthetic-csrf'}:null,
    createElement:tag=>{const el=new Element(tag);created.push(el);return el;}};
  const fetch=(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,reply:(status,payload)=>resolve({ok:status>=200&&status<300,json:async()=>payload}),invalidJSON:()=>resolve({ok:true,json:async()=>{throw new Error('bad JSON');}}),reject}));
  vm.runInNewContext(source,{document,fetch,Error,TypeError,console});
  return {get:id=>nodes.get(id),created,requests};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('saved review renders actionable guidance and evidence with no request or HTML interpretation',()=>{
  const value=review();value.summary='<img src=x onerror=alert(1)>';value.interview[0].exampleQuestion='<script>pergunta literal</script>';
  const h=harness({saved:value});
  assert.equal(h.requests.length,0);
  const text=h.get('learning-review-content').textContent;
  for(const phrase of [value.summary,value.interview[0].exampleQuestion,'Compare o achado','Minha hipótese registrada','Descreva como reavaliar','Não solicitado','Solicitado','Desnecessário']) {
    assert.ok(text.includes(phrase),phrase);
  }
  assert.ok(!h.created.some(el=>['SCRIPT','IMG'].includes(el.tagName)));
  assert.equal(h.get('learning-review-content').hidden,false);
  assert.equal(h.get('saved-score').textContent,'5/100');
});

test('missing review makes one same-origin POST with CSRF and prevents simultaneous retries',async()=>{
  const h=harness();
  assert.equal(h.requests.length,1);
  const request=h.requests[0];
  assert.equal(request.url,'/api/sessoes/synthetic-session/parecer');
  assert.equal(request.options.method,'POST');assert.equal(request.options.credentials,'same-origin');
  assert.equal(request.options.headers['X-CSRFToken'],'synthetic-csrf');
  assert.equal(request.options.headers['Content-Type'],'application/json');
  assert.equal(request.options.body,'{}','Only the server supplies the conversation and grading inputs.');
  assert.equal(h.get('learning-review').attributes['aria-busy'],'true');
  assert.equal(h.get('learning-review-retry').disabled,true);
  h.get('learning-review-retry').click();assert.equal(h.requests.length,1);
  request.reply(200,{learningReview:review()});await settle();
  assert.equal(h.get('learning-review').attributes['aria-busy'],undefined);
  assert.match(h.get('learning-review-status').textContent,/nota permanece a mesma/);
  h.get('learning-review-retry').click();assert.equal(h.requests.length,1);
});

test('reasoning and plan show optional examples as literal text, preserving older reviews',()=>{
  const absent=harness({saved:review()});
  assert.equal(absent.get('learning-review-content').hidden,false);
  assert.ok(!absent.get('learning-review-content').textContent.includes('UMA FORMA DE ABORDAR'));
  const value=review();
  value.diagnosis.exampleQuestion='<img src=x> Como esses achados sustentam a hipótese?';
  value.plan.exampleQuestion='Como você pretende acompanhar a resposta ao plano?';
  const h=harness({saved:value});
  const text=h.get('learning-review-content').textContent;
  assert.equal(text.split('UMA FORMA DE ABORDAR').length-1,2);
  assert.ok(text.includes(value.diagnosis.exampleQuestion));assert.ok(text.includes(value.plan.exampleQuestion));
  assert.ok(!h.created.some(el=>el.tagName==='IMG'));assert.equal(h.requests.length,0);
  assert.equal(h.get('saved-score').textContent,'5/100');
  value.diagnosis.exampleQuestion='';value.plan.exampleQuestion='  ';
  const empty=harness({saved:value});
  assert.equal(empty.get('learning-review-content').hidden,false);
  assert.ok(!empty.get('learning-review-content').textContent.includes('UMA FORMA DE ABORDAR'));
});

test('reasoning examples reject non-text and oversized values without rendering partial advice',()=>{
  for(const field of ['diagnosis','plan']) for(const example of [null,5,false,{},[], 'x'.repeat(501)]) {
    const value=review();value[field].exampleQuestion=example;
    const h=harness({saved:value});
    assert.equal(h.get('learning-review-content').hidden,true,`${field}: ${typeof example}`);
    assert.match(h.get('learning-review-error-text').textContent,/incompleto/);
    assert.equal(h.get('saved-score').textContent,'5/100');
  }
});

test('provider failure exposes a specific retry and never silently repeats or changes the score',async()=>{
  const h=harness();h.requests[0].reply(503,{error:'O serviço demorou. Tente novamente.'});await settle();
  assert.equal(h.get('learning-review-error').hidden,false);
  assert.equal(h.get('learning-review-error-text').textContent,'O serviço demorou. Tente novamente.');
  assert.equal(h.get('learning-review-retry').disabled,false);assert.equal(h.requests.length,1);
  h.get('learning-review-retry').click();assert.equal(h.requests.length,2);
  h.requests[1].reply(200,{learningReview:review()});await settle();
  assert.equal(h.get('learning-review-error').hidden,true);
  assert.equal(h.get('saved-score').textContent,'5/100');
});

test('incomplete or unsafe response structure shows an error instead of partial advice',async()=>{
  const h=harness();const value=review();value.interview[0].status='__proto__';
  h.requests[0].reply(200,{learningReview:value});await settle();
  assert.equal(h.get('learning-review-content').hidden,true);
  assert.match(h.get('learning-review-error-text').textContent,/incompleto/);
  assert.equal(h.get('learning-review-retry').disabled,false);
  assert.equal(h.get('saved-score').textContent,'5/100');
});

test('non-JSON and network failures restore retry without an automatic loop',async()=>{
  const h=harness();h.requests[0].invalidJSON();await settle();
  assert.match(h.get('learning-review-error-text').textContent,/ler o parecer/);
  h.get('learning-review-retry').click();h.requests[1].reject(new TypeError('synthetic network failure'));await settle();
  assert.match(h.get('learning-review-error-text').textContent,/conexão/);
  assert.equal(h.requests.length,2);assert.equal(h.get('learning-review-retry').disabled,false);
});

test('demo does not request a provider review but can display an existing saved review',()=>{
  const empty=harness({demo:true});
  assert.equal(empty.requests.length,0);assert.match(empty.get('learning-review-status').textContent,/demonstração/);
  empty.get('learning-review-retry').click();assert.equal(empty.requests.length,0);
  const existing=harness({demo:true,saved:review()});
  assert.equal(existing.requests.length,0);assert.equal(existing.get('learning-review-content').hidden,false);
});

test('loading the asset without its result section performs no work',()=>{
  const h=harness({present:false});assert.equal(h.requests.length,0);assert.equal(h.created.length,0);
});
