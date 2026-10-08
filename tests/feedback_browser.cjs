"use strict";
// Real Chrome/CDP E2E for the isolated 8221 preview. Run after pytest finishes.
// Requires only Node and Chrome; artifacts/user-data stay outside the repository.
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const artifacts = path.resolve(process.argv[2]);
if (!process.argv[2] || artifacts.startsWith(path.resolve(__dirname, '..') + path.sep)) throw Error('Choose an external artifact directory');
fs.mkdirSync(artifacts, {recursive: true});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;
async function run() {
  browser = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
    '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
    '--disable-default-apps', '--remote-debugging-port=9221', '--remote-allow-origins=http://127.0.0.1:9221',
    '--user-data-dir=' + path.join(artifacts, 'chrome-profile'), 'about:blank'], {windowsHide: true, stdio:'ignore'});
  let target;
  for (let i=0; i<100; i++) {
    try { target = (await (await fetch('http://127.0.0.1:9221/json')).json()).find(x => x.type==='page'); if(target) break; } catch {}
    await delay(100);
  }
  assert.ok(target, 'Chrome CDP started');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=> {socket.onopen=resolve; socket.onerror=reject;});
  let id=0, failNext=false, pauseNext=false, paused=null;
  const pending=new Map(), errors=[];
  function cmd(method,params={}) { return new Promise((resolve,reject)=> { const next=++id; pending.set(next,{resolve,reject}); socket.send(JSON.stringify({id:next,method,params})); }); }
  socket.onmessage = event => {
    const data=JSON.parse(event.data);
    if(data.id) {const p=pending.get(data.id); pending.delete(data.id); if(data.error) p.reject(Error(data.error.message)); else p.resolve(data.result);}
    if(data.method==='Runtime.exceptionThrown') errors.push(data.params.exceptionDetails.text);
    if(data.method==='Fetch.requestPaused') {
      const requestId=data.params.requestId;
      if(failNext) {failNext=false; void cmd('Fetch.failRequest',{requestId,errorReason:'Failed'});}
      else if(pauseNext) {pauseNext=false; paused=requestId;}
      else void cmd('Fetch.continueRequest',{requestId});
    }
  };
  await cmd('Runtime.enable'); await cmd('Page.enable');
  await cmd('Network.enable'); await cmd('Network.setBypassServiceWorker',{bypass:true});
  async function evaluate(expression) {
    const result=await cmd('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(result.exceptionDetails) throw Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  }
  async function wait(expression) { for(let i=0;i<150;i++) {if(await evaluate(expression)) return; await delay(100);} throw Error('Timed out: '+expression); }
  async function click(selector) {
    await wait(`(()=>{const n=document.querySelector(${JSON.stringify(selector)});return n && !n.disabled && n.getClientRects().length})()`);
    await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);
    const r=await evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    await cmd('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...r});
    await cmd('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...r});
  }
  async function fill(selector,value) { await evaluate(`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.value=${JSON.stringify(value)};n.dispatchEvent(new Event('input',{bubbles:true}))})()`); }
  async function snapshot(name) {const result=await cmd('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(artifacts,name+'.png'),Buffer.from(result.data,'base64'));}
  await cmd('Page.navigate',{url:'http://127.0.0.1:8221/#/auth'});
  await wait(`document.querySelector('#login-form') && !document.querySelector('#login-form').hidden`);
  await fill('#login-form [name="username"]','feedback_student');
  await fill('#login-form [name="password"]','feedback-only-password');
  await click('#login-form button[type="submit"]');
  await wait(`!document.querySelector('#app').hidden && document.querySelector('#app').getAttribute('aria-busy')==='false'`);
  const results=[];
  for(const [label,width,height] of [['desktop',1440,1000],['phone',390,844]]) {
    await cmd('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:label==='phone'});
    await evaluate(`showView('forum')`);
    await wait(`document.querySelector('.board-open') && !document.querySelector('#forum-list').hidden`);
    await click('#board-r-open-btn');
    await wait(`document.querySelector('.board-row') && document.querySelector('#forum-list').getAttribute('aria-busy') !== 'true'`);
    await click('.board-pill.is-open');
    await wait(`!document.querySelector('#forum-detail').hidden && !document.querySelector('#forum-comment-form').hidden`);
    await click('#forum-back');
    await wait(`document.querySelector('.board-row') && !document.querySelector('#forum-list').hidden`);
    await click('.board-row .board-excerpt');
    await wait(`!document.querySelector('#forum-detail').hidden`);
    await click('#forum-back');
    await wait(`document.querySelector('.board-row') && !document.querySelector('#forum-list').hidden`);
    await click('.board-stat');
    await wait(`!document.querySelector('#forum-detail').hidden && !document.querySelector('#forum-comment-form').hidden`);
    await fill('#forum-comment-body',label+' 首次回复');
    await click('#forum-comment-form button[type="submit"]');
    await wait(`document.querySelector('#forum-comments').textContent.includes(${JSON.stringify(label+' 首次回复')})`);
    await snapshot(label+'-reply');
    await evaluate(`showForumList()`);
    // Set all tab, then simulate one failed GET with the real fetch failure path.
    await click('[data-board-tab="all"]');
    await wait(`document.querySelector('.board-row')`);
    await cmd('Fetch.enable',{patterns:[{urlPattern:'http://127.0.0.1:8221/api/posts/*',requestStage:'Request'}]});
    failNext=true;
    await click('.board-open');
    await wait(`!document.querySelector('#forum-open-retry').hidden`);
    assert.match(await evaluate(`document.querySelector('#forum-open-status').textContent`),/打不开/);
    await click('#forum-open-retry');
    await wait(`!document.querySelector('#forum-detail').hidden`);
    await cmd('Fetch.disable');
    await click('.forum-comment-meta .profile-author');
    await wait(`document.querySelector('#profile-dialog').open && document.querySelector('#profile-content h3').textContent!=='个人资料'`);
    assert.equal(await evaluate(`document.querySelector('#profile-content').textContent.includes('复习次数')`),false);
    await snapshot(label+'-profile');
    await click('.profile-close');
    await evaluate(`window.Profile.open(user.id)`);
    await wait(`document.querySelector('.profile-rank-name')`);
    await fill('.profile-rank-name','李四');
    await evaluate(`document.querySelector('.profile-rank-name').closest('form').requestSubmit()`);
    await wait(`document.querySelector('#profile-title').textContent==='李四'`);
    await snapshot(label+'-rank-settings');
    await click('.profile-close');
    await evaluate(`showView('leaderboard')`);
    await wait(`document.querySelector('#rank-yesterday-list').textContent.includes('张三')`);
    await snapshot(label+'-rank');
    assert.equal(await evaluate(`document.documentElement.scrollWidth > innerWidth`),false,'No horizontal overflow');
    results.push({viewport:label,clickToReply:true,failedOpenRetry:true,publicCard:true,rankName:true,width});
  }
  assert.deepEqual(errors,[],'No uncaught browser errors');
  fs.writeFileSync(path.join(artifacts,'results.json'),JSON.stringify({results,errors},null,2));
  console.log(JSON.stringify({results,errors}));
  socket.close();
}
run().then(()=>{browser?.kill();}).catch(error=>{ console.error(error.stack); browser?.kill();process.exitCode=1;});
