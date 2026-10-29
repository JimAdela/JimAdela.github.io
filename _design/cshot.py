import json,subprocess,time,urllib.request,base64,shutil,tempfile,websocket
URL='https://jimadela.github.io/2026/10/29/generative-adversarial-networks-minimax/'
CH=r"C:/Program Files/Google/Chrome/Application/chrome.exe"
prof=tempfile.mkdtemp()
p=subprocess.Popen([CH,"--headless=new","--disable-gpu","--remote-debugging-port=9336","--user-data-dir="+prof,"--no-first-run","about:blank"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(2.5);v=None
for _ in range(40):
    try:v=json.load(urllib.request.urlopen("http://127.0.0.1:9336/json/version"));break
    except Exception:time.sleep(0.3)
ws=websocket.create_connection(v["webSocketDebuggerUrl"],timeout=60);i=[0]
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
send("Emulation.setDeviceMetricsOverride",{"width":1200,"height":900,"deviceScaleFactor":1,"mobile":False},s)
send("Page.navigate",{"url":URL},s);time.sleep(4)
r=send("Runtime.evaluate",{"expression":"""(function(){
  var b=document.querySelector('.code-copy'); if(b) b.style.opacity='1';
  var l=document.querySelector('.code-lang'); if(l) l.style.opacity='0';
  var el=document.querySelector('.codeblock'); var rect=el.getBoundingClientRect();
  return JSON.stringify({y: Math.round(rect.top+window.pageYOffset), h: Math.round(rect.height),
    meta: document.querySelector('.meta-row').innerText.replace(/\\s+/g,' ')});
})()""","returnByValue":True},s)
d=json.loads(r["result"]["result"]["value"]); print(d)
shot=send("Page.captureScreenshot",{"format":"png","captureBeyondViewport":True,
 "clip":{"x":180,"y":max(0,d["y"]-30),"width":840,"height":min(d["h"]+60,700),"scale":2}},s)
open('cb.png','wb').write(base64.b64decode(shot["result"]["data"]))
print("saved")
ws.close();p.terminate();time.sleep(0.3);shutil.rmtree(prof,ignore_errors=True)
