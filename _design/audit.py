import json,subprocess,time,urllib.request,sys,shutil,tempfile,websocket
URL=sys.argv[1] if len(sys.argv)>1 else 'https://jimadela.github.io/2026/10/29/generative-adversarial-networks-minimax/'
CH=r"C:/Program Files/Google/Chrome/Application/chrome.exe"
prof=tempfile.mkdtemp()
p=subprocess.Popen([CH,"--headless=new","--disable-gpu","--remote-debugging-port=9334","--user-data-dir="+prof,"--no-first-run","about:blank"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(2.5);v=None
for _ in range(40):
    try:v=json.load(urllib.request.urlopen("http://127.0.0.1:9334/json/version"));break
    except Exception:time.sleep(0.3)
ws=websocket.create_connection(v["webSocketDebuggerUrl"],timeout=90);i=[0]
def send(m,pr=None,sid=None):
    i[0]+=1;msg={"id":i[0],"method":m,"params":pr or {}}
    if sid:msg["sessionId"]=sid
    ws.send(json.dumps(msg))
    while True:
        r=json.loads(ws.recv())
        if r.get("id")==i[0]:return r
t=send("Target.createTarget",{"url":"about:blank"})["result"]["targetId"]
s=send("Target.attachToTarget",{"targetId":t,"flatten":True})["result"]["sessionId"]
send("Page.enable",{},s)
send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":900,"deviceScaleFactor":1,"mobile":False},s)
send("Page.navigate",{"url":URL},s);time.sleep(5)
JS = r"""
(function(){
  const out=[];
  const DYN=/::?(hover|focus|active|visited|link|focus-visible|focus-within|target|first-line|first-letter|before|after|selection|placeholder|marker|-webkit-[a-z-]+|-moz-[a-z-]+|disabled|checked|open)/g;
  for (const sheet of document.styleSheets){
    if(!sheet.href) continue;
    if(!/(bootstrap\.min|hux-blog\.min|syntax|font-awesome)/.test(sheet.href)) continue;
    let total=0,used=0,usedSel=[];
    const walk=(rules)=>{
      for(const r of rules){
        if(r.cssRules && r.conditionText!==undefined){ walk(r.cssRules); continue; }
        if(r.selectorText===undefined) continue;
        total++;
        const clean=r.selectorText.replace(DYN,'');
        let hit=false;
        try{ hit=!!document.querySelector(clean); }catch(e){ hit=false; }
        if(hit){ used++; if(usedSel.length<60) usedSel.push(r.selectorText.slice(0,70)); }
      }
    };
    try{ walk(sheet.cssRules); }catch(e){ out.push({href:sheet.href,total:-1,used:-1}); continue; }
    out.push({href:sheet.href.split('/').pop(),total:total,used:used,sel:usedSel});
  }
  const imgs=[...document.images];
  return JSON.stringify({
    sheets:out,
    img:{total:imgs.length,noalt:imgs.filter(i=>!i.alt).length,nolazy:imgs.filter(i=>i.loading!=='lazy').length,noDim:imgs.filter(i=>!i.width||!i.height).length},
    nodes:document.getElementsByTagName('*').length,
    domDepth:(function(){let d=0,e=document.body;while(e&&e.children.length){e=e.children[0];d++;}return d;})()
  });
})()
"""
r=send("Runtime.evaluate",{"expression":JS,"returnByValue":True},s)
print(r["result"]["result"]["value"])
ws.close();p.terminate();time.sleep(0.3);shutil.rmtree(prof,ignore_errors=True)
