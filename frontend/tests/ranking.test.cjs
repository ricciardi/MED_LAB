/** Ranking refresh uses synthetic server payloads; no real network or database. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../medlab/static/ranking.js'), 'utf8');

class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.attributes={};this._text='';this.replacements=0;this.selectors=new Map(); }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(_) { throw new Error('Rankings must not interpret names as HTML'); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.replacements++;this._text='';this.children=[...nodes]; }
  setAttribute(name,value) { this.attributes[name]=value; }
  querySelector(selector) { return this.selectors.get(selector)||null; }
}

function payload() {
  return {
    leaderboards:Object.fromEntries(['basic','clinical'].map(key=>[key, {title:key==='basic'?'Casos Básicos':'Casos Clínicos',minimum_cases:5,entries:[]} ])),
    personal_rankings:Object.fromEntries(['basic','clinical'].map(key=>[key, {rank:null,average_score:null,distinct_cases:0,consultations:0,eligible:false,remaining_cases:5,minimum_cases:5} ])),
  };
}

function harness({publicBoards=true,personal=true,endpoint='/medrounds/api/ranking'}={}) {
  const nodes=new Map(),ids=new Map(),created=[],requests=[],intervals=[],timeouts=new Map(),listeners={};
  let nextTimeout=0;
  nodes.set('script[data-ranking-url]',{dataset:{rankingUrl:endpoint}});
  for(const key of ['basic','clinical']) {
    if(publicBoards) {
      const content=new Element();content.textContent=`Initial ${key}`;
      nodes.set(`[data-ranking-content="${key}"]`,content);
      nodes.set(`[data-ranking-minimum="${key}"]`,new Element());
      ids.set(`ranking-title-${key}`,new Element());
    }
    if(personal) {
      const card=new Element();
      for(const selector of ['[data-ranking-personal-count]','[data-ranking-personal-value]']) { const child=new Element();child.textContent='Initial personal';card.selectors.set(selector,child);card.append(child); }
      nodes.set(`[data-ranking-personal="${key}"]`,card);
    }
  }
  const document={hidden:false,querySelector:selector=>nodes.get(selector)||null,getElementById:id=>ids.get(id)||null,
    createElement:tag=>{const node=new Element(tag);created.push(node);return node;},addEventListener:(type,fn)=>{listeners[type]=fn;}};
  const fetch=(url,options)=>new Promise((resolve,reject)=>{
    requests.push({url,options,reply:(status,data)=>resolve({ok:status>=200&&status<300,json:async()=>data}),reject});
    options.signal.addEventListener('abort',()=>reject(new Error('Aborted')));
  });
  vm.runInNewContext(source,{document,fetch,AbortController,console,
    setInterval:(fn,ms)=>{intervals.push({fn,ms});},
    setTimeout:(fn,ms)=>{timeouts.set(++nextTimeout,{fn,ms});return nextTimeout;},clearTimeout:id=>timeouts.delete(id)});
  return {document,requests,created,intervals,timeouts,get:selector=>nodes.get(selector),
    tick:()=>intervals.forEach(timer=>timer.fn()),visibility:hidden=>{document.hidden=hidden;listeners.visibilitychange?.();}};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('server HTML is retained initially; absent widgets create no polling',()=>{
  const h=harness();assert.equal(h.requests.length,0);assert.equal(h.intervals.length,1);assert.equal(h.intervals[0].ms,60000);
  assert.equal(h.get('[data-ranking-content="basic"]').textContent,'Initial basic');
  const empty=harness({publicBoards:false,personal:false});assert.equal(empty.intervals.length,0);assert.equal(empty.requests.length,0);
});

test('only visible pages poll, visibility refresh is immediate, and requests cannot overlap',async()=>{
  const h=harness();h.visibility(true);h.tick();assert.equal(h.requests.length,0);
  h.visibility(false);assert.equal(h.requests.length,1);
  h.tick();h.visibility(false);assert.equal(h.requests.length,1);
  const call=h.requests[0];assert.equal(call.url,'/medrounds/api/ranking');assert.equal(call.options.credentials,'same-origin');assert.equal(call.options.cache,'no-store');
  assert.equal(call.options.method,undefined);assert.equal(call.options.body,undefined);
  call.reply(200,payload());await settle();h.tick();assert.equal(h.requests.length,2);
});

test('table update preserves competition ranks and safely renders abbreviated names',async()=>{
  const h=harness();const data=payload();
  data.leaderboards.basic.entries=[
    {rank:1,display_name:'<img src=x onerror=alert(1)>',average_score:91.25,distinct_cases:5,is_current_user:true},
    {rank:1,display_name:'Bia S.',average_score:91.25,distinct_cases:8,is_current_user:false},
    {rank:3,display_name:'Caio M.',average_score:86,distinct_cases:6,is_current_user:false},
  ];
  h.tick();h.requests[0].reply(200,data);await settle();
  const table=h.get('[data-ranking-content="basic"]').children[0];
  assert.equal(table.tagName,'TABLE');assert.equal(table.children[0].tagName,'CAPTION');
  assert.ok(table.textContent.includes('<img src=x onerror=alert(1)>Você91,25'));
  assert.equal(h.created.some(node=>node.tagName==='IMG'),false);
  const rows=table.children[2].children;
  assert.deepEqual(rows.map(row=>row.children[0].textContent),['1º','1º','3º']);
  assert.equal(rows[0].children[1].attributes.scope,'row');assert.equal(rows[0].className,'ranking-current');
});

test('unchanged responses do not recreate tables and empty rankings can become populated',async()=>{
  const h=harness();const data=payload();h.tick();h.requests[0].reply(200,data);await settle();
  const content=h.get('[data-ranking-content="basic"]');assert.match(content.textContent,/pelo menos 5 casos distintos/);
  const previous=content.children[0],changes=content.replacements;
  h.tick();h.requests[1].reply(200,data);await settle();assert.equal(content.children[0],previous);assert.equal(content.replacements,changes);
  data.leaderboards.basic.entries=[{rank:1,display_name:'Ana P.',average_score:80,distinct_cases:5,is_current_user:false}];
  h.tick();h.requests[2].reply(200,data);await settle();assert.equal(content.children[0].tagName,'TABLE');
});

test('personal ranking transitions from formation to a global position, including a valid zero average',async()=>{
  const h=harness({publicBoards:false});const data=payload();data.personal_rankings.basic.distinct_cases=4;data.personal_rankings.basic.remaining_cases=1;
  h.tick();h.requests[0].reply(200,data);await settle();
  const card=h.get('[data-ranking-personal="basic"]');assert.match(card.textContent,/4 casos distintos.*Em formação.*Falta 1 caso distinto.*mínimo 5/);
  Object.assign(data.personal_rankings.basic,{rank:24,average_score:0,distinct_cases:5,eligible:true,remaining_cases:0});
  h.tick();h.requests[1].reply(200,data);await settle();assert.match(card.textContent,/24º lugarMédia 0,00\/100/);assert.ok(!card.textContent.includes('Em formação'));
});

test('failures and malformed data preserve the last display and allow a later refresh',async()=>{
  const h=harness();h.tick();h.requests[0].reply(503,{});await settle();
  const content=h.get('[data-ranking-content="basic"]');assert.equal(content.textContent,'Initial basic');
  const bad=payload();bad.leaderboards.basic.entries=[{rank:1,display_name:'Invalid',average_score:101,distinct_cases:5,is_current_user:false}];
  h.tick();h.requests[1].reply(200,bad);await settle();assert.equal(content.textContent,'Initial basic');
  h.tick();h.requests[2].reject(new Error('network unavailable'));await settle();assert.equal(content.textContent,'Initial basic');
  h.tick();h.requests[3].reply(200,payload());await settle();assert.match(content.textContent,/trajetória está começando/);
});

test('a hanging request is aborted after ten seconds and does not permanently block updates',async()=>{
  const h=harness();h.tick();const timer=[...h.timeouts.values()][0];assert.equal(timer.ms,10000);
  timer.fn();await settle();assert.equal(h.requests[0].options.signal.aborted,true);assert.equal(h.timeouts.size,0);
  h.tick();assert.equal(h.requests.length,2);
});

test('a response arriving while hidden is not applied',async()=>{
  const h=harness();h.tick();h.visibility(true);h.requests[0].reply(200,payload());await settle();
  assert.equal(h.get('[data-ranking-content="basic"]').textContent,'Initial basic');
  h.visibility(false);assert.equal(h.requests.length,2);
});
