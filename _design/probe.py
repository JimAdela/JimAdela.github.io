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
  const m=document.querySelector('.masthead');
  const p=document.querySelector('.progress');
  const cs=p?getComputedStyle(p):null;
  return JSON.stringify({
    mastheadH: m?Math.round(m.getBoundingClientRect().height):null,
    progress:{mb:cs?cs.marginBottom:null, bg:cs?cs.backgroundColor:null, ov:cs?cs.overflow:null, h:cs?cs.height:null, radius:cs?cs.borderRadius:null, shadow:cs?cs.boxShadow:null},
    navH: document.querySelector('.masthead-in')?Math.round(document.querySelector('.masthead-in').getBoundingClientRect().height):null,
    firstTextY: document.querySelector('.post-head')?Math.round(document.querySelector('.post-head').getBoundingClientRect().top):null
  });
})()
"""
r=send("Runtime.evaluate",{"expression":JS,"returnByValue":True},s)
print(r["result"]["result"]["value"])
ws.close();p.terminate();time.sleep(0.3);shutil.rmtree(prof,ignore_errors=True)
