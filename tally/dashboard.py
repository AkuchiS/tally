"""Local web dashboard — one self-contained page served from the standard library.

`tally serve` starts a loopback HTTP server (default 127.0.0.1:8848) that renders
`engine.status()`: a live gauge per tracked subscription, per-model 7-day usage bars, a
spend row, an activity heatmap, and a level/streak — for ANY provider/model you plug in.
No cloud, no build step, no external assets: the HTML/CSS/JS is inlined below and polls
/api/status. Colors follow the dataviz method — reserved status ramp (always shown with the
% and a state label) + a single-hue sequential ramp for the heatmap.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import store as S, engine, sources

PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>tally</title>
<style>
:root{--bg:#0c0f12;--card:#14181d;--card2:#0f1317;--fg:#e8edf2;--dim:#8a97a6;--line:#232a31;
--accent:#8A2BE2;--ok:#3fb950;--warn:#d29922;--serious:#e0843a;--crit:#f85149;--track:#242b33}
:root[data-theme=light],:root:not([data-theme=dark]).light{}
@media(prefers-color-scheme:light){:root{--bg:#f6f8fa;--card:#fff;--card2:#f2f5f8;--fg:#1b2027;
--dim:#5a6673;--line:#e2e8ee;--ok:#2da44e;--warn:#bf8700;--serious:#d9730d;--crit:#cf222e;--track:#e6ebf1}}
:root[data-theme=dark]{--bg:#0c0f12;--card:#14181d;--card2:#0f1317;--fg:#e8edf2;--dim:#8a97a6;
--line:#232a31;--ok:#3fb950;--warn:#d29922;--serious:#e0843a;--crit:#f85149;--track:#242b33}
:root[data-theme=light]{--bg:#f6f8fa;--card:#fff;--card2:#f2f5f8;--fg:#1b2027;--dim:#5a6673;
--line:#e2e8ee;--ok:#2da44e;--warn:#bf8700;--serious:#d9730d;--crit:#cf222e;--track:#e6ebf1}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;padding:20px;max-width:1040px;margin:auto}
a{color:var(--accent)}
.top{display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:9px;font-size:18px;font-weight:700}
.dot{width:14px;height:14px;border-radius:50%;background:conic-gradient(from 0deg,var(--accent),#4f9dff,var(--accent))}
.chip{font-size:12px;font-weight:600;padding:3px 10px;border-radius:20px;border:1px solid var(--line);color:var(--dim);white-space:nowrap}
.chip.lv{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--line))}
.chip.streak{color:var(--serious)}
.spacer{flex:1}
button.icon{background:var(--card);border:1px solid var(--line);color:var(--fg);width:34px;height:34px;
border-radius:9px;cursor:pointer;font-size:15px}
.pill{display:inline-flex;align-items:center;gap:8px;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:8px 12px;font-size:13px;margin-bottom:14px}
.pill b{color:var(--fg)}.pdot{width:8px;height:8px;border-radius:50%;background:var(--ok)}
.grid{display:grid;gap:12px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:15px}
.kpi .lbl{font-size:12px;color:var(--dim)}.kpi .val{font-size:24px;font-weight:700;letter-spacing:-.5px}
.kpi .sub{font-size:11px;color:var(--dim)}
.accts{grid-template-columns:repeat(auto-fit,minmax(240px,1fr));margin-bottom:14px}
.acct h3{margin:0 0 2px;font-size:15px}.acct .plan{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
.gwrap{display:flex;flex-direction:column;gap:12px;margin:14px 0 6px}
.gauge{position:relative;width:130px;height:130px;flex:0 0 auto;margin:0 auto}
.gauge .ctr{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.gauge .num{font-size:27px;font-weight:700;letter-spacing:-1px}.gauge .num small{font-size:14px;color:var(--dim)}
.gauge .cap{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.4px;margin-top:2px}
.wlist{width:100%}
.w{margin:9px 0}.w .wt{display:flex;justify-content:space-between;font-size:12px;gap:8px}
.w .wt span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.w .wt .rt{color:var(--dim);white-space:nowrap;flex:0 0 auto}
.bar{height:7px;border-radius:6px;background:var(--track);overflow:hidden;margin-top:4px}
.bar>i{display:block;height:100%;border-radius:6px;min-width:2px;transition:width .4s}
.sec{font-size:11px;color:var(--dim);margin-top:6px}
.panel h2{font-size:12px;letter-spacing:.6px;color:var(--dim);text-transform:uppercase;margin:0 0 12px;font-weight:600}
.panel h2 .rt{float:right;text-transform:none;letter-spacing:0}
.mrow{display:flex;align-items:center;gap:10px;margin:8px 0}
.mrow .mn{width:150px;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mrow .mb{flex:1}.mrow .mp{width:52px;text-align:right;font-size:12px;color:var(--dim)}
.hm{display:grid;grid-template-columns:34px repeat(24,1fr);gap:3px;align-items:center}
.hm .hl{font-size:10px;color:var(--dim);text-align:right;padding-right:4px}
.hm .cell{aspect-ratio:1;border-radius:3px;background:var(--track)}
.hx{display:grid;grid-template-columns:34px repeat(24,1fr);gap:3px;margin-top:4px}
.hx span{font-size:9px;color:var(--dim);text-align:center}
.alert{border-left:3px solid var(--crit);padding:7px 11px;margin:6px 0;border-radius:0 8px 8px 0;font-size:13px;
background:color-mix(in srgb,var(--crit) 12%,transparent)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}
@media(max-width:720px){.two{grid-template-columns:1fr}.gwrap{flex-direction:column}.mrow .mn{width:110px}}
.muted{color:var(--dim);font-size:12px}
</style></head><body>
<div class=top>
  <div class=brand><span class=dot></span> tally</div>
  <span class="chip lv" id=lv>—</span>
  <span class="chip streak" id=streak></span>
  <span class=spacer></span>
  <button class=icon id=theme title="Toggle theme">◐</button>
  <button class=icon id=refresh title="Refresh">⟳</button>
</div>
<div class=pill><span class=pdot id=pdot></span><span id=peak>—</span></div>
<div class="grid kpis" id=kpis></div>
<div id=alerts></div>
<div class="grid accts" id=accts></div>
<div class=two>
  <div class="card panel"><h2>Usage by model <span class=rt>last 7 days</span></h2><div id=models></div></div>
  <div class="card panel"><h2>Activity <span class=rt id=peakh></span></h2><div class=hm id=hm></div><div class=hx id=hx></div></div>
</div>
<div class=muted id=foot></div>
<script>
const SC={ok:'var(--ok)',warn:'var(--warn)',serious:'var(--serious)',critical:'var(--crit)',over:'var(--crit)',unknown:'var(--dim)'};
const DOW=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
const $=id=>document.getElementById(id);
function rin(t,now){if(!t)return'';let m=Math.max(0,(t-now)/60|0);return m>=1440?(m/1440|0)+'d':m>=60?(m/60|0)+'h'+String(m%60).padStart(2,'0')+'m':m+'m'}
function cvar(v){ // resolve 'var(--x)' to a concrete color (SVG attrs don't resolve var())
 let m=/var\((--[\w-]+)\)/.exec(v||''); if(!m)return v||'#888';
 return getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim()||'#888';
}
function gauge(w){ // svg ring; pct or raw
 let pct=w&&w.used_percent, r=52, c=2*Math.PI*r;
 let off=pct==null?c:c*(1-Math.min(100,pct)/100);
 let hero=pct==null?`${(w&&w.used)||0}<small> ${(w&&w.unit)||''}</small>`:`${Math.round(pct)}<small>%</small>`;
 let ringcol=cvar(pct==null?'var(--accent)':SC[(w&&w.state)||'unknown']), tr=cvar('var(--track)'), dim=pct==null?.35:1;
 return `<div class=gauge><svg viewBox="0 0 120 120" width=130 height=130>
   <circle cx=60 cy=60 r=${r} fill=none stroke-width=10 style="stroke:${tr}"/>
   <circle cx=60 cy=60 r=${r} fill=none stroke-width=10 stroke-linecap=round
     stroke-dasharray="${c}" stroke-dashoffset="${off}" transform="rotate(-90 60 60)" style="stroke:${ringcol};opacity:${dim}"/>
 </svg><div class=ctr><div class=num>${hero}</div><div class=cap>${w?w.label:''}</div></div></div>`;
}
function bar(pct,col){return `<div class=bar><i style="width:${Math.min(100,pct||0)}%;background:${col}"></i></div>`}
function heat(grid){
 let max=1;grid.forEach(r=>r.forEach(v=>{if(v>max)max=v}));
 let html='';
 for(let d=0;d<7;d++){html+=`<span class=hl>${DOW[d]}</span>`;
  for(let h=0;h<24;h++){let v=grid[d][h],o=v?(.12+.88*v/max):0;
   html+=`<div class=cell title="${DOW[d]} ${h}:00 · ${v}" style="background:${v?`color-mix(in srgb,var(--accent) ${Math.round(o*100)}%,var(--track))`:'var(--track)'}"></div>`}}
 $('hm').innerHTML=html;
 let hx='<span></span>';for(let h=0;h<24;h++)hx+=`<span>${h%3?'':h}</span>`;$('hx').innerHTML=hx;
}
async function tick(){
 let s;try{s=await(await fetch('api/status')).json()}catch(e){return}
 let now=s.now, pr=s.progress||{};
 $('lv').textContent=`Lv ${pr.level||0} · ${pr.tier||'New'}`;
 $('streak').textContent=pr.streak?`🔥 ${pr.streak}-day streak`:'';
 let sp=s.spend||{};
 $('peak').innerHTML=s.peak_hour==null?'No activity yet':`Your peak hour ~<b>${String(s.peak_hour).padStart(2,'0')}:00</b> — spent <b>$${(sp.today||0).toFixed(2)}</b> today`;
 $('kpis').innerHTML=[['Today',sp.today,''],['This week',sp.week,''],['This month',sp.month,''],['Forecast',sp.forecast_month,'month-end']]
  .map(([k,v,sub])=>`<div class="card kpi"><div class=lbl>${k}</div><div class=val>$${(v||0).toFixed(2)}</div><div class=sub>${sub}</div></div>`).join('');
 let al=s.alerts||[];$('alerts').innerHTML=al.map(x=>`<div class=alert>⚠ ${x.message}</div>`).join('');
 $('accts').innerHTML=(s.accounts||[]).map(a=>{
  let g=a.gauge, secs=(a.windows||[]).filter(w=>!g||w.key!=g.key);
  let sec=secs.map(w=>{let capped=w.used_percent!=null;
   let disp=capped?`${Math.round(w.used_percent)}%`:`${w.used} ${w.unit}`;
   return `<div class=w><div class=wt><span>${w.label}</span><span class=rt>${disp} · ${rin(w.reset_at,now)}</span></div>
   ${capped?bar(w.used_percent,SC[w.state]):''}</div>`}).join('');
  return `<div class="card acct"><h3>${a.label||a.id}</h3><div class=plan>${a.provider} · ${a.plan}</div>
   <div class=gwrap>${gauge(g)}<div class=wlist>${sec||'<div class=muted>no secondary limits</div>'}</div></div>
   ${g?`<div class=sec>resets ${rin(g.reset_at,now)}${g.used_percent==null?' · no cap set':''}</div>`:''}</div>`;
 }).join('')||'<div class=card>No subscriptions tracked. Run <code>tally track claude max20</code> then <code>tally sync</code>.</div>';
 let mm=s.models_7d||[],mx=Math.max(1,...mm.map(m=>m.percent));
 $('models').innerHTML=mm.length?mm.map(m=>`<div class=mrow><span class=mn title="${m.model}">${m.model}</span>
   <span class=mb>${bar(m.percent/mx*100,'var(--accent)')}</span><span class=mp>${m.percent}%</span></div>`).join('')
   :'<div class=muted>No usage in the last 7 days.</div>';
 $('peakh').textContent=s.peak_hour==null?'':`peak ~${String(s.peak_hour).padStart(2,'0')}:00`;
 heat(s.heatmap||Array.from({length:7},()=>Array(24).fill(0)));
 $('foot').textContent=`${s.events||0} events tracked · local-first · updates every 4s`;
}
// theme toggle (persisted)
(function(){let t=localStorage.getItem('tally-theme');if(t)document.documentElement.dataset.theme=t;
 $('theme').onclick=()=>{let d=document.documentElement,n=(d.dataset.theme==='dark')?'light':'dark';
  d.dataset.theme=n;localStorage.setItem('tally-theme',n)};
 $('refresh').onclick=tick;})();
tick();setInterval(tick,4000);
</script></body></html>"""


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            try:
                sources.ingest(S)          # refresh local reads on each poll (idempotent)
            except Exception:
                pass
            st = engine.status(S)
            st["alerts"] = engine.alerts(S)
            self._send(json.dumps(st), "application/json")
        else:
            self._send(PAGE)


def serve(port=8848, host="127.0.0.1"):
    httpd = ThreadingHTTPServer((host, port), _H)
    print("tally dashboard → http://%s:%d  (Ctrl-C to stop)" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
