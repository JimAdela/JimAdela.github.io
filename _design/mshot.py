import json,subprocess,time,urllib.request,base64,shutil,tempfile,websocket
URL='https://jimadela.github.io/series/'
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
send("Emulation.setDeviceMetricsOverride",{"width":390,"height":844,"deviceScaleFactor":2,"mobile":True},s)
send("Page.navigate",{"url":URL},s);time.sleep(4)
r=send("Runtime.evaluate",{"expression":"JSON.stringify({vw:innerWidth,docW:document.documentElement.scrollWidth,navW:Math.round(document.querySelector('.site-nav').getBoundingClientRect().width),markW:Math.round(document.querySelector('.site-mark').getBoundingClientRect().width)})","returnByValue":True},s)
print(r["result"]["result"]["value"]); d={"y":0,"h":1400}
shot=send("Page.captureScreenshot",{"format":"png","captureBeyondViewport":True,
 "clip":{"x":0,"y":0,"width":390,"height":1400,"scale":2}},s)
open('m_series.png','wb').write(base64.b64decode(shot["result"]["data"]))
print("saved")
ws.close();p.terminate();time.sleep(0.3);shutil.rmtree(prof,ignore_errors=True)
