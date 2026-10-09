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
let browser, browserSocket;
async function run() {
  browser = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
    '--headless=new', '--disable-gpu', '--disable-gpu-sandbox', '--no-sandbox', '--no-first-run', '--disable-background-networking',
    '--disable-default-apps', '--remote-debugging-port=9221', '--remote-allow-origins=http://127.0.0.1:9221',
    '--user-data-dir=' + path.join(artifacts, 'chrome-profile'), 'about:blank'], {windowsHide: true, stdio:['ignore','ignore','pipe']});
  browser.stderr.on('data', chunk => fs.appendFileSync(path.join(artifacts,'chrome.log'),chunk));
  let target;
  for (let i=0; i<100; i++) {
    try { target = (await (await fetch('http://127.0.0.1:9221/json')).json()).find(x => x.type==='page'); if(target) break; } catch {}
    await delay(100);
  }
  assert.ok(target, 'Chrome CDP started');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  browserSocket = socket;
  await new Promise((resolve,reject)=> {socket.onopen=resolve; socket.onerror=reject;});
  let id=0, failNext=false, pauseNext=false, paused=null;
  const pending=new Map(), errors=[];
  function cmd(method,params={}) { return new Promise((resolve,reject)=> {
    const next=++id;
    const timeout=setTimeout(()=> {pending.delete(next);reject(Error('CDP timed out: '+method));},20000);
    pending.set(next,{resolve(value){clearTimeout(timeout);resolve(value);},reject(error){clearTimeout(timeout);reject(error);}});
    socket.send(JSON.stringify({id:next,method,params}));
  }); }
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
  await cmd('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await cmd('Network.enable'); await cmd('Network.setBypassServiceWorker',{bypass:true});
  async function evaluate(expression) {
    const result=await cmd('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(result.exceptionDetails) throw Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  }
  async function wait(expression) { for(let i=0;i<150;i++) {if(await evaluate(expression)) return; await delay(100);} throw Error('Timed out: '+expression+'; notice='+await evaluate(`document.querySelector('#notice')?.textContent`)); }
  async function click(selector) {
    if (selector.startsWith('.board-')) await wait(`!window.Board.snapshot().loading`);
    await wait(`(()=>{const n=document.querySelector(${JSON.stringify(selector)});return n && !n.disabled && n.getClientRects().length})()`);
    await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);
    const r=await evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    await cmd('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...r});
    await cmd('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...r});
  }
  async function fill(selector,value) { await evaluate(`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.value=${JSON.stringify(value)};n.dispatchEvent(new Event('input',{bubbles:true}))})()`); }
  async function snapshot(name) {const result=await cmd('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(artifacts,name+'.png'),Buffer.from(result.data,'base64'));}
  async function login() {
    await evaluate(`location.hash='#/auth'`);
    await wait(`document.querySelector('#login-form') && !document.querySelector('#login-form').hidden`);
    await fill('#login-form [name="username"]','feedback_student');
    await fill('#login-form [name="password"]','feedback-only-password');
    await click('#login-form button[type="submit"]');
    await wait(`!document.querySelector('#app').hidden && document.querySelector('#app').getAttribute('aria-busy')==='false'`);
  }
  await cmd('Page.navigate',{url:'http://127.0.0.1:8221/#/auth'});
  await login();
  const results=[];
  for(const [label,width,height] of [['desktop',1440,1000],['phone',390,844]]) {
    await cmd('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:label==='phone'});
    await evaluate(`api('/api/posts',{method:'POST',body:JSON.stringify({title:${JSON.stringify(label+' 零回复验证帖子')},body:'浏览器隔离测试：首次回复',zone:'算法'})})`);
    await evaluate(`showView('forum')`);
    await wait(`document.querySelector('.board-open') && !document.querySelector('#forum-list').hidden`);
    await cmd('Fetch.enable',{patterns:[{urlPattern:'http://127.0.0.1:8221/api/posts?*',requestStage:'Request'}]});
    pauseNext=true;
    await click('#board-r-open-btn');
    for(let i=0;i<100 && !paused;i++) await delay(50);
    assert.ok(paused,'Unanswered list request is paused');
    await click('[data-board-tab="all"]');
    await wait(`window.Board.snapshot().selection.tab === 'all' && !window.Board.snapshot().loading`);
    await cmd('Fetch.continueRequest',{requestId:paused}); paused=null;
    await delay(200);
    assert.equal(await evaluate(`window.Board.snapshot().selection.tab`),'all','Late unanswered response cannot replace the current tab');
    await cmd('Fetch.disable');
    // A delayed search must not remove the native press target or cancel its navigation.
    await evaluate(`document.querySelector('.board-open').scrollIntoView({block:'center'})`);
    const searching = await evaluate(`(()=>{window.feedbackSearchTitle=document.querySelector('.board-open');const r=feedbackSearchTitle.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    await fill('#forum-search','二分');
    await cmd('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...searching});
    await delay(300);
    assert.equal(await evaluate(`window.feedbackSearchTitle.isConnected`),true,'Delayed search preserves the pressed title');
    await cmd('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...searching});
    await wait(`!document.querySelector('#forum-detail').hidden && !document.querySelector('#forum-comment-form').hidden`);
    await click('#forum-back');
    await wait(`document.querySelector('.board-open') && !window.Board.snapshot().loading`);
    await fill('#forum-search','');
    // Reproduce a list refresh settling between native pointer down and click.
    for (const keep of [true,false]) {
    await evaluate(`document.querySelector('.board-open').scrollIntoView({block:'center'})`);
    const held = await evaluate(`(()=>{const n=document.querySelector('.board-open');window.feedbackPressedTitle=n;const r=n.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    await cmd('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...held});
    await cmd('Fetch.enable',{patterns:[{urlPattern:'http://127.0.0.1:8221/api/posts',requestStage:'Request'}]});
    pauseNext=true;
    await evaluate(`void window.Board.load({keep:${keep}})`);
    for(let i=0;i<100 && !paused;i++) await delay(50);
    assert.ok(paused,'Retained list refresh is paused');
    await cmd('Fetch.continueRequest',{requestId:paused}); paused=null;
    await wait(`!window.Board.snapshot().loading`);
    assert.equal(await evaluate(`window.feedbackPressedTitle.isConnected`),true,'Pressed title survives list refresh');
    await cmd('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...held});
    await wait(`!document.querySelector('#forum-detail').hidden && !document.querySelector('#forum-comment-form').hidden`);
    await cmd('Fetch.disable');
    await click('#forum-back');
    await wait(`document.querySelector('.board-open') && !window.Board.snapshot().loading`);
    }
    await click('#board-r-open-btn');
    await wait(`document.querySelector('.board-row') && document.querySelector('#forum-list').getAttribute('aria-busy') !== 'true'`);
    await evaluate(`document.querySelector('.board-open').focus()`);
    await click('.board-open');
    await wait(`!document.querySelector('#forum-detail').hidden && !document.querySelector('#forum-comment-form').hidden`);
    await click('#forum-back');
    await wait(`document.querySelector('.board-row') && !document.querySelector('#forum-list').hidden`);
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
    await cmd('Fetch.enable',{patterns:[{urlPattern:'http://127.0.0.1:8221/api/posts/*',requestStage:'Request'}]});
    failNext=true;
    await click('#forum-comment-form button[type="submit"]');
    await wait(`document.querySelector('#forum-comment-status').textContent.includes('网络') && !document.querySelector('#forum-comment-form button[type="submit"]').disabled`);
    assert.equal(await evaluate(`document.querySelector('#forum-comment-body').value`),label+' 首次回复');
    await click('#forum-comment-form button[type="submit"]');
    await wait(`document.querySelector('#forum-comments').textContent.includes(${JSON.stringify(label+' 首次回复')})`);
    await cmd('Fetch.disable');
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
    // A real pending detail response must not reopen a post after a tab switch.
    await click('#forum-back');
    await wait(`document.querySelector('.board-open') && !document.querySelector('#forum-list').hidden`);
    await cmd('Fetch.enable',{patterns:[{urlPattern:'http://127.0.0.1:8221/api/posts/*',requestStage:'Request'}]});
    pauseNext=true;
    await click('.board-open');
    for(let i=0;i<100 && !paused;i++) await delay(50);
    assert.ok(paused,'Detail request is paused: '+JSON.stringify(await evaluate(`({state:window.Board.snapshot(),detailHidden:document.querySelector('#forum-detail').hidden,status:document.querySelector('#forum-open-status').textContent,focus:document.activeElement.outerHTML})`)));
    assert.match(await evaluate(`document.querySelector('#forum-open-status').textContent`),/正在打开/);
    await click('[data-board-tab="unanswered"]');
    await wait(`window.Board.snapshot().selection.tab === 'unanswered' && !window.Board.snapshot().loading`);
    await cmd('Fetch.continueRequest',{requestId:paused}); paused=null;
    await delay(200);
    assert.equal(await evaluate(`document.querySelector('#forum-detail').hidden`),true,'Late detail stays on list');
    await cmd('Fetch.disable');
    // Fail the list itself, then retry and enter one of its zero-reply posts.
    await cmd('Fetch.enable',{patterns:[{urlPattern:'http://127.0.0.1:8221/api/posts',requestStage:'Request'}]});
    failNext=true;
    await click('[data-board-tab="all"]');
    await wait(`!document.querySelector('#board-notice').hidden`);
    assert.match(await evaluate(`document.querySelector('#board-notice').textContent`),/网络.*重试/);
    await click('#board-notice button');
    await wait(`document.querySelector('.board-open') && !window.Board.snapshot().loading`);
    await cmd('Fetch.disable');
    await click('.board-open');
    await wait(`!document.querySelector('#forum-detail').hidden`);
    // Shrinking the real browser viewport simulates a mobile keyboard; no physical keyboard claim.
    await click('#forum-comments button[aria-label^="回复 "]');
    await cmd('Emulation.setDeviceMetricsOverride',{width,height:400,deviceScaleFactor:1,mobile:label==='phone'});
    await wait(`(()=>{const r=document.querySelector('#forum-comment-body').getBoundingClientRect();return r.top >= visualViewport.offsetTop && r.bottom <= visualViewport.offsetTop + visualViewport.height})()`);
    await snapshot(label+'-keyboard-viewport');
    await cmd('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:label==='phone'});
    await click('.forum-comment-meta .profile-author');
    await wait(`document.querySelector('#profile-dialog').open && document.querySelector('#profile-content h3').textContent!=='个人资料'`);
    assert.equal(await evaluate(`document.querySelector('#profile-content').textContent.includes('复习次数')`),false);
    await snapshot(label+'-profile');
    await click('.profile-close');
    await evaluate(`window.Profile.open(user.id)`);
    await wait(`document.querySelector('.profile-rank-name')`);
    const rankName = label === 'desktop' ? '李四' : '赵六';
    await fill('.profile-rank-name',rankName);
    await evaluate(`document.querySelector('.profile-rank-name').closest('form').requestSubmit()`);
    await wait(`document.querySelector('#profile-title').textContent===${JSON.stringify(rankName)}`);
    await snapshot(label+'-rank-settings');
    await click('.profile-close');
    await evaluate(`showView('leaderboard')`);
    await wait(`document.querySelector('#rank-yesterday-list').textContent.includes('张三')`);
    await snapshot(label+'-rank');
    assert.equal(await evaluate(`document.documentElement.scrollWidth > innerWidth`),false,'No horizontal overflow');
    await evaluate(`showView('forum')`);
    await wait(`document.querySelector('.board-open') && !document.querySelector('#forum-list').hidden`);
    // Expiry can first be encountered while filtering the list, before opening a post.
    await cmd('Network.clearBrowserCookies');
    await click('#board-r-open-btn');
    await wait(`document.querySelector('#app').hidden && location.hash === '#/auth' && document.querySelector('#notice').textContent.includes('登录已过期')`);
    await snapshot(label+'-expired-list');
    await login();
    await evaluate(`showView('forum')`);
    await wait(`document.querySelector('.board-open') && !window.Board.snapshot().loading && !document.querySelector('#forum-list').hidden`);
    await cmd('Network.clearBrowserCookies');
    await click('.board-open');
    await wait(`document.querySelector('#app').hidden && document.querySelector('#notice').textContent.includes('登录已过期')`);
    await snapshot(label+'-expired-open');
    await login();
    await evaluate(`showView('forum')`);
    await wait(`document.querySelector('.board-open') && !document.querySelector('#forum-list').hidden`);
    await click('.board-open');
    await wait(`!document.querySelector('#forum-detail').hidden`);
    await fill('#forum-comment-body','登录过期回复测试');
    await cmd('Network.clearBrowserCookies');
    await click('#forum-comment-form button[type="submit"]');
    await wait(`document.querySelector('#app').hidden && document.querySelector('#notice').textContent.includes('登录已过期')`);
    await snapshot(label+'-expired-reply');
    await login();
    results.push({viewport:label,clickToReply:true,searchClickRace:true,failedReplyRetry:true,failedOpenRetry:true,failedListRetry:true,staleDetailDiscarded:true,keyboardViewport:true,expiredList:true,expiredOpen:true,expiredReply:true,publicCard:true,rankName:true,width});
  }
  assert.deepEqual(errors,[],'No uncaught browser errors');
  fs.writeFileSync(path.join(artifacts,'results.json'),JSON.stringify({results,errors},null,2));
  console.log(JSON.stringify({results,errors}));
  socket.close();
}
run().then(()=>{browserSocket?.close();browser?.kill();}).catch(error=>{ console.error(error.stack);browserSocket?.close();browser?.kill();process.exitCode=1;});
