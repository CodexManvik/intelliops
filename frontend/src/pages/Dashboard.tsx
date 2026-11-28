import { useState, useMemo } from "react";

type Page = "landing" | "audit-log" | "product" | "docs" | "dashboard";
type SidebarSection = "overview" | "alerts" | "services" | "metrics" | "audit-log";

const SERVICES = [
  { name: "api-gateway",       status: "degraded", latency: "135ms", uptime: "99.2%", uptimePct: 99.2, requests: "2.4k/s", errors: "3.2%",  p99: "2340ms", region: "us-east-1", type: "HTTP Gateway" },
  { name: "postgres-primary",  status: "warning",  latency: "—",     uptime: "99.8%", uptimePct: 99.8, requests: "1.1k/s", errors: "0.1%",  p99: "—",      region: "us-east-1", type: "Database" },
  { name: "cdn-edge",          status: "healthy",  latency: "18ms",  uptime: "100%",  uptimePct: 100,  requests: "14k/s",  errors: "0.0%",  p99: "42ms",   region: "global",    type: "CDN" },
  { name: "reporting-service", status: "warning",  latency: "240ms", uptime: "99.6%", uptimePct: 99.6, requests: "80/s",   errors: "0.4%",  p99: "490ms",  region: "us-east-1", type: "Microservice" },
  { name: "cache-layer",       status: "healthy",  latency: "4ms",   uptime: "100%",  uptimePct: 100,  requests: "8.2k/s", errors: "0.0%",  p99: "9ms",    region: "us-east-1", type: "Cache" },
  { name: "k8s-ingress",       status: "healthy",  latency: "22ms",  uptime: "100%",  uptimePct: 100,  requests: "3.1k/s", errors: "0.0%",  p99: "55ms",   region: "us-east-1", type: "Ingress" },
  { name: "prometheus",        status: "healthy",  latency: "—",     uptime: "100%",  uptimePct: 100,  requests: "—",      errors: "0.0%",  p99: "—",      region: "us-east-1", type: "Monitoring" },
  { name: "otel-collector",    status: "healthy",  latency: "—",     uptime: "100%",  uptimePct: 100,  requests: "—",      errors: "0.0%",  p99: "—",      region: "us-east-1", type: "Telemetry" },
];
const SVC_STATUS = {
  healthy:  { dot: "#22C55E", bg: "#F0FDF4", text: "#16A34A", label: "Healthy"  },
  warning:  { dot: "#F59E0B", bg: "#FFFBEB", text: "#D97706", label: "Warning"  },
  degraded: { dot: "#EF4444", bg: "#FEF2F2", text: "#DC2626", label: "Degraded" },
};
const SVC_SPARKLINES: Record<string, number[]> = {
  "api-gateway":       [92,93,95,96,94,90,85,80,76,72,70,68,66,65,64],
  "postgres-primary":  [99,99,99,99,98,97,96,95,94,93,92,91,90,90,89],
  "cdn-edge":          [100,100,100,100,100,100,100,100,100,100,100,100,100,100,100],
  "reporting-service": [99,99,99,99,99,98,98,97,97,96,96,96,96,96,96],
  "cache-layer":       [100,100,100,100,100,100,100,100,100,100,100,100,100,100,100],
  "k8s-ingress":       [100,100,100,100,100,100,100,99,100,100,100,100,100,100,100],
  "prometheus":        [100,100,100,100,100,100,100,100,100,100,100,100,100,100,100],
  "otel-collector":    [100,100,100,100,100,100,100,100,100,100,100,100,100,100,100],
};
const ALL_ALERTS = [
  { id: "ALT-0091", level: "crit", service: "api-gateway",       msg: "P99 latency > 2s — ongoing",              time: "14m ago",    ack: false },
  { id: "ALT-0090", level: "warn", service: "postgres-primary",  msg: "Connection pool 87% utilized",             time: "2h ago",     ack: true  },
  { id: "ALT-0089", level: "warn", service: "reporting-service", msg: "Response time > 200ms threshold",          time: "2h 5m ago",  ack: false },
  { id: "ALT-0088", level: "info", service: "cdn-edge",          msg: "Cache hit rate recovered to 93%",          time: "3h ago",     ack: true  },
  { id: "ALT-0087", level: "ok",   service: "k8s-ingress",       msg: "Pod restart resolved — 3 replicas ok",     time: "5h ago",     ack: true  },
  { id: "ALT-0086", level: "crit", service: "postgres-primary",  msg: "Connection pool exhaustion — 24/25 used",  time: "5h 30m ago", ack: true  },
  { id: "ALT-0085", level: "warn", service: "cdn-edge",          msg: "Cache hit rate dropped below 70%",         time: "6h ago",     ack: true  },
  { id: "ALT-0084", level: "info", service: "api-gateway",       msg: "Deploy api-v2.3.1 detected — monitoring",  time: "6h 12m ago", ack: true  },
  { id: "ALT-0083", level: "warn", service: "otel-collector",    msg: "Metrics push lag > 30s",                   time: "7h ago",     ack: true  },
  { id: "ALT-0082", level: "ok",   service: "prometheus",        msg: "Scrape targets all healthy — 47/47",       time: "7h 30m ago", ack: true  },
  { id: "ALT-0081", level: "info", service: "cache-layer",       msg: "Memory usage at 72% — within threshold",   time: "8h ago",     ack: true  },
  { id: "ALT-0080", level: "warn", service: "reporting-service", msg: "Long-running job detected — 8m duration",  time: "9h ago",     ack: true  },
];
const ALERT_DOT: Record<string,string> = { crit:"#EF4444", warn:"#F59E0B", info:"#6366F1", ok:"#22C55E" };
const ALERT_BG:  Record<string,string> = { crit:"#FEF2F2", warn:"#FFFBEB", info:"#EEF2FF", ok:"#F0FDF4" };
const ALERT_TXT: Record<string,string> = { crit:"#DC2626", warn:"#D97706", info:"#4338CA", ok:"#16A34A" };
const ALERT_LBL: Record<string,string> = { crit:"CRITICAL",warn:"WARNING", info:"INFO",    ok:"OK" };
const METRIC_SERIES = [
  { label:"CPU Utilization",           unit:"avg %",  color:"#2F5FF6", currentVal:"45%",   trend:"↑ from 38%",   trendUp:true,  data:[38,40,39,37,35,34,36,42,48,55,58,62,60,58,76,82,74,68,65,62,60,57,52,48,45] },
  { label:"P99 Latency — api-gateway", unit:"ms",     color:"#EF4444", currentVal:"128ms", trend:"↓ recovering", trendUp:false, data:[85,87,86,84,83,82,85,88,91,94,96,98,92,90,2340,2100,1450,980,560,340,200,160,140,130,128] },
  { label:"Error Rate",                unit:"%",      color:"#F59E0B", currentVal:"0.1%",  trend:"↓ normalized", trendUp:false, data:[0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.2,0.2,0.3,0.3,0.3,0.2,0.2,2.8,3.2,2.1,1.4,0.8,0.5,0.3,0.2,0.1,0.1,0.1] },
  { label:"Request Rate",              unit:"req/s",  color:"#22C55E", currentVal:"2.4k",  trend:"stable",       trendUp:false, data:[2100,2180,2220,2190,2310,2400,2350,2280,2190,2050,1980,2100,2240,2370,2410,2380,2290,2200,2310,2390,2410,2450,2420,2410,2400] },
  { label:"DB Connections",            unit:"active", color:"#8B5CF6", currentVal:"18",    trend:"↓ recovering", trendUp:false, data:[8,9,8,10,13,17,21,23,24,24,24,23,22,20,18,17,16,15,14,13,14,15,16,17,18] },
  { label:"Cache Hit Rate",            unit:"%",      color:"#06B6D4", currentVal:"93%",   trend:"↑ recovered",  trendUp:false, data:[94,93,94,92,78,65,61,62,65,70,75,80,85,88,90,91,92,93,93,93,93,92,93,93,93] },
  { label:"Memory Usage",              unit:"avg %",  color:"#EC4899", currentVal:"58%",   trend:"stable",       trendUp:false, data:[52,53,53,54,55,56,57,58,58,58,59,59,58,58,57,57,58,58,58,59,58,58,58,58,58] },
  { label:"Network I/O",               unit:"MB/s",   color:"#F97316", currentVal:"142",   trend:"↑ high load",  trendUp:true,  data:[80,82,85,84,88,92,98,105,112,118,122,128,132,138,142,140,138,135,132,130,128,132,138,140,142] },
];
const SIDEBAR_ITEMS: { id: SidebarSection; label: string; badge?: number; icon: React.ReactNode }[] = [
  { id:"overview",  label:"Overview",  icon:<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.25"/><rect x="9" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.25"/><rect x="2" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.25"/><rect x="9" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.25"/></svg> },
  { id:"alerts",    label:"Alerts",    badge:14, icon:<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 2a4.5 4.5 0 0 0-4.5 4.5v2.25L2 11h12l-1.5-2.25V6.5A4.5 4.5 0 0 0 8 2Z" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round"/><path d="M6.5 12a1.5 1.5 0 0 0 3 0" stroke="currentColor" strokeWidth="1.25"/></svg> },
  { id:"services",  label:"Services",  icon:<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="5" width="12" height="8" rx="1.5" stroke="currentColor" strokeWidth="1.25"/><path d="M5 5V4a3 3 0 0 1 6 0v1" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round"/><circle cx="8" cy="9" r="1.5" stroke="currentColor" strokeWidth="1.25"/></svg> },
  { id:"metrics",   label:"Metrics",   icon:<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 12 5.5 7l3 3.5L12 4l2 3" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  { id:"audit-log", label:"Audit Log", icon:<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="3" y="2" width="10" height="12" rx="1.5" stroke="currentColor" strokeWidth="1.25"/><path d="M5.5 5.5h5M5.5 8h5M5.5 10.5h3" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round"/></svg> },
];

function Chip({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return <button onClick={onClick} style={{ padding:"5px 12px", borderRadius:7, fontSize:12, fontWeight:active?600:500, background:active?"#2F5FF6":"#F3F4F6", color:active?"#FFFFFF":"#6B7280", border:active?"1px solid #2F5FF6":"1px solid #E4E5E9", cursor:"pointer", transition:"all 0.15s", whiteSpace:"nowrap" }}>{label}</button>;
}

function MiniChart({ data, color, h=60 }: { data:number[]; color:string; h?:number }) {
  const W=300, P=4, iW=W-P*2, iH=h-P*2;
  const mx=Math.max(...data), mn=Math.min(...data), rng=mx-mn||1;
  const px=(i:number)=>P+(i/(data.length-1))*iW;
  const py=(v:number)=>P+(1-(v-mn)/rng)*iH;
  const pts=data.map((v,i)=>`${px(i)},${py(v)}`).join(" ");
  const area=`M${px(0)},${h-P} `+data.map((v,i)=>`L${px(i)},${py(v)}`).join(" ")+` L${px(data.length-1)},${h-P} Z`;
  const gid=`mc${color.replace("#","")}`;
  return <svg width="100%" viewBox={`0 0 ${W} ${h}`} style={{display:"block"}}><defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity="0.18"/><stop offset="100%" stopColor={color} stopOpacity="0"/></linearGradient></defs><path d={area} fill={`url(#${gid})`}/><polyline points={pts} fill="none" stroke={color} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"/></svg>;
}

function FullChart({ label, data, color, unit, currentVal, trend, trendUp }: { label:string; data:number[]; color:string; unit:string; currentVal:string; trend:string; trendUp?:boolean }) {
  const W=500, H=130, PT=8, PB=22, PL=8, PR=8, iW=W-PL-PR, iH=H-PT-PB;
  const mx=Math.max(...data), mn=Math.min(...data), rng=mx-mn||1;
  const px=(i:number)=>PL+(i/(data.length-1))*iW;
  const py=(v:number)=>PT+(1-(v-mn)/rng)*iH;
  const pts=data.map((v,i)=>`${px(i)},${py(v)}`).join(" ");
  const area=`M${px(0)},${H-PB} `+data.map((v,i)=>`L${px(i)},${py(v)}`).join(" ")+` L${px(data.length-1)},${H-PB} Z`;
  const gid=`fc${label.replace(/\W/g,"")}`;
  const sorted=[...data].sort((a,b)=>a-b), med=sorted[Math.floor(sorted.length/2)];
  const pk=data.findIndex(v=>v>med*3);
  const tl=["00:00","06:00","12:00","18:00","now"], tp=[0,6,12,18,data.length-1];
  return (
    <div style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:12,padding:"16px 20px"}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:12}}>
        <div><div style={{fontSize:12,color:"#6B7280",fontWeight:500,marginBottom:4}}>{label}</div><div style={{display:"flex",alignItems:"baseline",gap:8}}><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:22,fontWeight:700,color:"#101114"}}>{currentVal}</span><span style={{fontSize:12,color:"#6B7280"}}>{unit}</span></div></div>
        <span style={{fontFamily:"JetBrains Mono, monospace",fontSize:11,padding:"3px 8px",borderRadius:6,background:trendUp?"#FEF2F2":"#F0FDF4",color:trendUp?"#DC2626":"#16A34A"}}>{trend}</span>
      </div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{display:"block",overflow:"visible"}}>
        <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity="0.15"/><stop offset="100%" stopColor={color} stopOpacity="0"/></linearGradient></defs>
        {[0.25,0.5,0.75].map((f,i)=><line key={i} x1={PL} y1={PT+f*iH} x2={W-PR} y2={PT+f*iH} stroke="#F3F4F6" strokeWidth="1"/>)}
        {pk>0&&<rect x={px(pk-1)} y={PT} width={px(pk+2)-px(pk-1)} height={iH} fill="#FEF2F2" opacity="0.5" rx="2"/>}
        <path d={area} fill={`url(#${gid})`}/><polyline points={pts} fill="none" stroke={color} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"/>
        {pk>0&&<circle cx={px(pk)} cy={py(data[pk])} r="3.5" fill="#EF4444"/>}
        {tp.map((ti,i)=><text key={i} x={px(Math.min(ti,data.length-1))} y={H-2} fontSize="9" fontFamily="JetBrains Mono, monospace" fill="#9CA3AF" textAnchor="middle">{tl[i]}</text>)}
      </svg>
    </div>
  );
}
function AlertsTab() {
  const [search, setSearch] = useState("");
  const [lvl, setLvl] = useState<"all"|"crit"|"warn"|"info"|"ok">("all");
  const [ack, setAck] = useState<"all"|"unacked"|"acked">("all");
  const filtered = useMemo(() => ALL_ALERTS.filter(a => {
    if (lvl!=="all" && a.level!==lvl) return false;
    if (ack==="unacked" && a.ack) return false;
    if (ack==="acked" && !a.ack) return false;
    if (search) { const q=search.toLowerCase(); return a.service.toLowerCase().includes(q)||a.msg.toLowerCase().includes(q)||a.id.toLowerCase().includes(q); }
    return true;
  }), [search, lvl, ack]);
  return (
    <div>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:20}}>
        <div><h1 style={{fontSize:20,fontWeight:800,color:"#101114",margin:"0 0 3px",letterSpacing:"-0.03em"}}>Alerts</h1><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:11,color:"#9CA3AF"}}>Mon 15 Jan 2024 · 14:37 UTC · Acme Corp / production</div></div>
        <div style={{display:"flex",alignItems:"center",gap:6,padding:"6px 12px",background:"#EEF2FF",border:"1px solid #C7D2FE",borderRadius:7}}><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:11,fontWeight:600,color:"#2F5FF6"}}>AI dedup active</span></div>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(4, 1fr)",gap:10,marginBottom:18}}>
        {[{lbl:"Critical",lv:"crit",c:"#EF4444"},{lbl:"Warning",lv:"warn",c:"#F59E0B"},{lbl:"Info",lv:"info",c:"#6366F1"},{lbl:"Resolved",lv:"ok",c:"#22C55E"}].map((x,i)=>{
          const cnt=ALL_ALERTS.filter(a=>a.level===x.lv).length;
          return <div key={i} style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:10,padding:"14px 16px"}}><div style={{fontSize:11,color:"#6B7280",fontWeight:500,marginBottom:6}}>{x.lbl}</div><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:24,fontWeight:700,color:x.c}}>{cnt}</div><div style={{marginTop:8,height:3,borderRadius:2,background:"#F3F4F6"}}><div style={{height:"100%",borderRadius:2,background:x.c,width:`${(cnt/ALL_ALERTS.length)*100}%`}}/></div></div>;
        })}
      </div>
      <div style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:12,padding:"12px 14px",marginBottom:14,display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
        <div style={{position:"relative"}}><svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{position:"absolute",left:9,top:"50%",transform:"translateY(-50%)"}}><circle cx="5.5" cy="5.5" r="4" stroke="#9CA3AF" strokeWidth="1.2"/><path d="m9 9 2.5 2.5" stroke="#9CA3AF" strokeWidth="1.2" strokeLinecap="round"/></svg><input type="text" placeholder="Search alerts…" value={search} onChange={e=>setSearch(e.target.value)} style={{paddingLeft:28,paddingRight:10,paddingTop:6,paddingBottom:6,border:"1px solid #E4E5E9",borderRadius:7,fontSize:12,color:"#101114",background:"#F7F8FA",width:200,fontFamily:"inherit",outline:"none"}} onFocus={e=>(e.currentTarget.style.borderColor="#2F5FF6")} onBlur={e=>(e.currentTarget.style.borderColor="#E4E5E9")}/></div>
        <div style={{width:1,height:22,background:"#E4E5E9"}}/>
        <div style={{display:"flex",gap:5}}>{(["all","crit","warn","info","ok"] as const).map(l=><Chip key={l} active={lvl===l} label={l==="all"?"All levels":ALERT_LBL[l]} onClick={()=>setLvl(l)}/>)}</div>
        <div style={{width:1,height:22,background:"#E4E5E9"}}/>
        <div style={{display:"flex",gap:5}}>{(["all","unacked","acked"] as const).map(a=><Chip key={a} active={ack===a} label={a==="all"?"All":a==="unacked"?"Unacknowledged":"Acknowledged"} onClick={()=>setAck(a)}/>)}</div>
        <div style={{marginLeft:"auto",fontFamily:"JetBrains Mono, monospace",fontSize:11,color:"#9CA3AF"}}>{filtered.length} alerts</div>
      </div>
      <div style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:12,overflow:"hidden"}}>
        <div style={{display:"grid",gridTemplateColumns:"90px 130px 1fr 90px 110px",padding:"9px 18px",borderBottom:"1px solid #E4E5E9",background:"#F7F8FA"}}>
          {["Level","Service","Message","Time","Status"].map(h=><div key={h} style={{fontSize:10,fontWeight:700,color:"#9CA3AF",letterSpacing:"0.05em",textTransform:"uppercase"}}>{h}</div>)}
        </div>
        {filtered.length===0
          ?<div style={{padding:"40px 18px",textAlign:"center",fontFamily:"JetBrains Mono, monospace",fontSize:12,color:"#9CA3AF"}}>No alerts match current filters</div>
          :filtered.map((a,i)=>(
            <div key={a.id} style={{display:"grid",gridTemplateColumns:"90px 130px 1fr 90px 110px",padding:"11px 18px",borderBottom:i<filtered.length-1?"1px solid #F3F4F6":"none",alignItems:"center",transition:"background 0.1s",background:a.level==="crit"&&!a.ack?"#FFFAFA":"transparent"}} onMouseEnter={e=>(e.currentTarget.style.background="#FAFBFF")} onMouseLeave={e=>(e.currentTarget.style.background=a.level==="crit"&&!a.ack?"#FFFAFA":"transparent")}>
              <div><span style={{display:"inline-flex",alignItems:"center",gap:5,background:ALERT_BG[a.level],color:ALERT_TXT[a.level],borderRadius:5,padding:"2px 7px",fontSize:9,fontWeight:700,fontFamily:"JetBrains Mono, monospace"}}><span style={{width:5,height:5,borderRadius:"50%",background:ALERT_DOT[a.level],display:"inline-block"}}/>{ALERT_LBL[a.level]}</span></div>
              <div style={{fontFamily:"JetBrains Mono, monospace",fontSize:11,color:"#374151",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{a.service}</div>
              <div><div style={{fontSize:12,color:"#101114"}}>{a.msg}</div><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:9,color:"#9CA3AF",marginTop:2}}>{a.id}</div></div>
              <div style={{fontFamily:"JetBrains Mono, monospace",fontSize:10,color:"#9CA3AF"}}>{a.time}</div>
              <div><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:10,fontWeight:600,padding:"2px 8px",borderRadius:5,background:a.ack?"#F0FDF4":"#FEF2F2",color:a.ack?"#16A34A":"#DC2626"}}>{a.ack?"Acknowledged":"Open"}</span></div>
            </div>
          ))
        }
      </div>
    </div>
  );
}

function ServicesTab() {
  const [filter, setFilter] = useState<"all"|"healthy"|"warning"|"degraded">("all");
  const [selected, setSelected] = useState<string|null>(null);
  const filtered = SERVICES.filter(s=>filter==="all"||s.status===filter);
  const sel = selected?SERVICES.find(s=>s.name===selected):null;
  return (
    <div>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:20}}>
        <div><h1 style={{fontSize:20,fontWeight:800,color:"#101114",margin:"0 0 3px",letterSpacing:"-0.03em"}}>Services</h1><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:11,color:"#9CA3AF"}}>{SERVICES.filter(s=>s.status==="healthy").length} healthy · {SERVICES.filter(s=>s.status==="warning").length} warning · {SERVICES.filter(s=>s.status==="degraded").length} degraded</div></div>
        <div style={{display:"flex",gap:6}}>{(["all","healthy","warning","degraded"] as const).map(f=><Chip key={f} active={filter===f} label={f==="all"?"All":f.charAt(0).toUpperCase()+f.slice(1)} onClick={()=>setFilter(f)}/>)}</div>
      </div>
      <div style={{display:"grid",gridTemplateColumns:sel?"1fr 360px":"1fr",gap:16}}>
        <div style={{display:"flex",flexDirection:"column",gap:8}}>
          {filtered.map(svc=>{
            const st=SVC_STATUS[svc.status as keyof typeof SVC_STATUS];
            const spark=SVC_SPARKLINES[svc.name]||[];
            const isSel=selected===svc.name;
            return (
              <div key={svc.name} onClick={()=>setSelected(isSel?null:svc.name)} style={{background:"#FFFFFF",border:`1px solid ${isSel?"#2F5FF6":"#E4E5E9"}`,borderRadius:12,padding:"16px 20px",cursor:"pointer",transition:"border-color 0.15s, box-shadow 0.15s",boxShadow:isSel?"0 0 0 3px rgba(47,95,246,0.08)":"none"}} onMouseEnter={e=>{if(!isSel)(e.currentTarget as HTMLElement).style.borderColor="#C7D2FE"}} onMouseLeave={e=>{if(!isSel)(e.currentTarget as HTMLElement).style.borderColor="#E4E5E9"}}>
                <div style={{display:"flex",alignItems:"center",gap:12}}>
                  <div style={{width:10,height:10,borderRadius:"50%",background:st.dot,flexShrink:0}}/>
                  <div style={{flex:1,minWidth:0}}><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:13,fontWeight:600,color:"#101114"}}>{svc.name}</div><div style={{fontSize:11,color:"#9CA3AF",marginTop:1}}>{svc.type} · {svc.region}</div></div>
                  <div style={{width:120}}><div style={{fontSize:10,color:"#9CA3AF",marginBottom:4}}>30d uptime</div><div style={{height:4,borderRadius:2,background:"#F3F4F6",overflow:"hidden"}}><div style={{height:"100%",borderRadius:2,background:st.dot,width:`${svc.uptimePct}%`}}/></div><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:10,color:"#6B7280",marginTop:3}}>{svc.uptime}</div></div>
                  <div style={{width:100}}><MiniChart data={spark} color={st.dot} h={36}/></div>
                  <div style={{width:70,textAlign:"right"}}><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:13,fontWeight:600,color:"#101114"}}>{svc.latency}</div><div style={{fontSize:10,color:"#9CA3AF"}}>latency</div></div>
                  <div style={{width:56,textAlign:"right"}}><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:13,fontWeight:600,color:svc.errors==="0.0%"?"#22C55E":svc.errors>"1%"?"#EF4444":"#F59E0B"}}>{svc.errors}</div><div style={{fontSize:10,color:"#9CA3AF"}}>errors</div></div>
                  <span style={{background:st.bg,color:st.text,borderRadius:6,padding:"3px 9px",fontSize:11,fontWeight:700,fontFamily:"JetBrains Mono, monospace"}}>{st.label}</span>
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{transform:isSel?"rotate(90deg)":"none",transition:"transform 0.15s"}}><path d="M5 3l4 4-4 4" stroke="#9CA3AF" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round"/></svg>
                </div>
              </div>
            );
          })}
        </div>
        {sel&&(()=>{
          const st=SVC_STATUS[sel.status as keyof typeof SVC_STATUS];
          const spark=SVC_SPARKLINES[sel.name]||[];
          const related=ALL_ALERTS.filter(a=>a.service===sel.name);
          return (
            <div style={{display:"flex",flexDirection:"column",gap:10}}>
              <div style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:12,padding:"18px 20px"}}>
                <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:14}}><div style={{width:10,height:10,borderRadius:"50%",background:st.dot}}/><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:14,fontWeight:700,color:"#101114"}}>{sel.name}</div><span style={{marginLeft:"auto",background:st.bg,color:st.text,borderRadius:6,padding:"3px 9px",fontSize:11,fontWeight:700,fontFamily:"JetBrains Mono, monospace"}}>{st.label}</span></div>
                <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,marginBottom:14}}>{[{l:"P99 Latency",v:sel.p99},{l:"Requests",v:sel.requests},{l:"Error Rate",v:sel.errors},{l:"Uptime (30d)",v:sel.uptime}].map(m=><div key={m.l}><div style={{fontSize:10,color:"#9CA3AF",marginBottom:2}}>{m.l}</div><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:14,fontWeight:700,color:"#101114"}}>{m.v}</div></div>)}</div>
                <div style={{fontSize:10,color:"#9CA3AF",marginBottom:4}}>Uptime trend (30d)</div><MiniChart data={spark} color={st.dot} h={50}/>
              </div>
              <div style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:12,padding:"16px 20px"}}>
                <div style={{fontSize:12,fontWeight:700,color:"#101114",marginBottom:10}}>Service Info</div>
                {[{k:"Type",v:sel.type},{k:"Region",v:sel.region},{k:"Stack",v:"Prometheus + OTel"}].map(r=><div key={r.k} style={{display:"flex",justifyContent:"space-between",paddingBottom:8,marginBottom:8,borderBottom:"1px solid #F3F4F6"}}><span style={{fontSize:12,color:"#6B7280"}}>{r.k}</span><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:11,color:"#101114"}}>{r.v}</span></div>)}
              </div>
              <div style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:12,overflow:"hidden"}}>
                <div style={{padding:"12px 16px",borderBottom:"1px solid #E4E5E9",fontSize:12,fontWeight:700,color:"#101114"}}>Recent Alerts</div>
                {related.length===0?<div style={{padding:"16px",fontFamily:"JetBrains Mono, monospace",fontSize:11,color:"#9CA3AF"}}>No recent alerts</div>:related.map((a,i)=><div key={a.id} style={{display:"flex",alignItems:"center",gap:8,padding:"9px 16px",borderBottom:i<related.length-1?"1px solid #F3F4F6":"none"}}><div style={{width:6,height:6,borderRadius:"50%",background:ALERT_DOT[a.level],flexShrink:0}}/><span style={{fontSize:11,color:"#374151",flex:1}}>{a.msg}</span><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:10,color:"#9CA3AF"}}>{a.time}</span></div>)}
              </div>
            </div>
          );
        })()}
      </div>
    </div>
  );
}

function MetricsTab() {
  const [search, setSearch] = useState("");
  const [tr, setTr] = useState<"1h"|"6h"|"24h"|"7d">("24h");
  const filtered = METRIC_SERIES.filter(m=>!search||m.label.toLowerCase().includes(search.toLowerCase()));
  return (
    <div>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:20}}>
        <div><h1 style={{fontSize:20,fontWeight:800,color:"#101114",margin:"0 0 3px",letterSpacing:"-0.03em"}}>Metrics</h1><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:11,color:"#9CA3AF"}}>{METRIC_SERIES.length} series · live · Prometheus-backed</div></div>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <div style={{position:"relative"}}><svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{position:"absolute",left:9,top:"50%",transform:"translateY(-50%)"}}><circle cx="5.5" cy="5.5" r="4" stroke="#9CA3AF" strokeWidth="1.2"/><path d="m9 9 2.5 2.5" stroke="#9CA3AF" strokeWidth="1.2" strokeLinecap="round"/></svg><input type="text" placeholder="Filter metrics…" value={search} onChange={e=>setSearch(e.target.value)} style={{paddingLeft:28,paddingRight:10,paddingTop:6,paddingBottom:6,border:"1px solid #E4E5E9",borderRadius:7,fontSize:12,color:"#101114",background:"#F7F8FA",width:180,fontFamily:"inherit",outline:"none"}} onFocus={e=>(e.currentTarget.style.borderColor="#2F5FF6")} onBlur={e=>(e.currentTarget.style.borderColor="#E4E5E9")}/></div>
          <div style={{display:"flex",background:"#F3F4F6",borderRadius:8,padding:3,gap:2}}>{(["1h","6h","24h","7d"] as const).map(t=><button key={t} onClick={()=>setTr(t)} style={{padding:"4px 10px",borderRadius:6,fontSize:12,fontWeight:tr===t?600:400,background:tr===t?"#FFFFFF":"transparent",color:tr===t?"#101114":"#6B7280",border:"none",cursor:"pointer",boxShadow:tr===t?"0 1px 3px rgba(16,17,20,0.1)":"none",transition:"all 0.15s"}}>{t}</button>)}</div>
        </div>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(4, 1fr)",gap:10,marginBottom:18}}>
        {[{l:"Healthy metrics",v:"6/8",c:"#22C55E"},{l:"Anomalies",v:"3",c:"#EF4444"},{l:"Scrape interval",v:"15s",c:"#2F5FF6"},{l:"Series tracked",v:"1,847",c:"#6366F1"}].map((x,i)=><div key={i} style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:10,padding:"14px 16px"}}><div style={{fontSize:11,color:"#6B7280",fontWeight:500,marginBottom:4}}>{x.l}</div><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:22,fontWeight:700,color:x.c}}>{x.v}</div></div>)}
      </div>
      {filtered.length===0
        ?<div style={{textAlign:"center",padding:48,fontFamily:"JetBrains Mono, monospace",fontSize:12,color:"#9CA3AF"}}>No metrics match "{search}"</div>
        :<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14}}>{filtered.map(m=><FullChart key={m.label} label={m.label} data={m.data} color={m.color} unit={m.unit} currentVal={m.currentVal} trend={m.trend} trendUp={m.trendUp}/>)}</div>
      }
    </div>
  );
}

function OverviewTab() {
  const cpuData=[38,40,39,37,35,34,36,42,48,55,58,62,60,58,76,82,74,68,65,62,60,57,52,48,45];
  const latData=[85,87,86,84,83,82,85,88,91,94,96,98,92,90,2340,2100,1450,980,560,340,200,160,140,130,128];
  const errData=[0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.2,0.2,0.3,0.3,0.3,0.2,0.2,2.8,3.2,2.1,1.4,0.8,0.5,0.3,0.2,0.1,0.1,0.1];
  return (
    <>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:24}}>
        <div><h1 style={{fontSize:20,fontWeight:800,color:"#101114",margin:"0 0 3px",letterSpacing:"-0.03em"}}>Overview</h1><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:11,color:"#9CA3AF"}}>Mon 15 Jan 2024 · 14:37 UTC · Acme Corp / production</div></div>
        <div style={{display:"flex",gap:8}}><div style={{display:"flex",alignItems:"center",gap:6,padding:"6px 12px",background:"#F0FDF4",border:"1px solid #BBF7D0",borderRadius:7}}><div style={{width:6,height:6,borderRadius:"50%",background:"#22C55E"}}/><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:11,fontWeight:600,color:"#16A34A"}}>AI ACTIVE</span></div><button style={{padding:"6px 12px",borderRadius:7,border:"1px solid #E4E5E9",background:"#FFFFFF",fontSize:12,fontWeight:500,color:"#374151",cursor:"pointer"}}>Last 24h ▾</button></div>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(4, 1fr)",gap:12,marginBottom:20}}>
        {[{label:"Monitored Services",value:"47",sub:"44 healthy · 3 degraded",color:"#2F5FF6",pct:"94%"},{label:"Alerts Today",value:"128",sub:"↓ 40% vs 7-day avg",color:"#F59E0B",pct:"60%"},{label:"Avg MTTRC",value:"4m 12s",sub:"AI-assisted root cause",color:"#22C55E",pct:"85%"},{label:"AI Anomalies",value:"3",sub:"Detected last 24h",color:"#6366F1",pct:"20%"}].map((s,i)=>(
          <div key={i} style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:10,padding:"16px 18px"}}><div style={{fontSize:12,color:"#6B7280",marginBottom:8,fontWeight:500}}>{s.label}</div><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:22,fontWeight:700,color:"#101114",marginBottom:4}}>{s.value}</div><div style={{fontSize:11,color:"#9CA3AF"}}>{s.sub}</div><div style={{marginTop:10,height:3,borderRadius:2,background:"#F3F4F6",overflow:"hidden"}}><div style={{height:"100%",borderRadius:2,background:s.color,width:s.pct}}/></div></div>
        ))}
      </div>
      <div style={{display:"flex",gap:12,marginBottom:20}}>
        <FullChart label="CPU Utilization" data={cpuData} color="#2F5FF6" unit="avg across cluster" currentVal="45%" trend="↑ from 38%" trendUp/>
        <FullChart label="P99 Latency — api-gateway" data={latData} color="#EF4444" unit="ms" currentVal="128ms" trend="↓ recovering" trendUp={false}/>
        <FullChart label="Error Rate" data={errData} color="#F59E0B" unit="%" currentVal="0.1%" trend="↓ normalized" trendUp={false}/>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12}}>
        <div style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:12,overflow:"hidden"}}>
          <div style={{padding:"14px 18px",borderBottom:"1px solid #E4E5E9",display:"flex",justifyContent:"space-between",alignItems:"center"}}><span style={{fontSize:13,fontWeight:700,color:"#101114"}}>Service Health</span><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:10,color:"#9CA3AF"}}>{SERVICES.filter(s=>s.status==="healthy").length}/{SERVICES.length} healthy</span></div>
          <div style={{padding:"8px 8px"}}>{SERVICES.map((svc,i)=>{const st=SVC_STATUS[svc.status as keyof typeof SVC_STATUS];return<div key={i} style={{display:"flex",alignItems:"center",gap:10,padding:"8px 10px",borderRadius:7,transition:"background 0.1s"}} onMouseEnter={e=>(e.currentTarget.style.background="#F7F8FA")} onMouseLeave={e=>(e.currentTarget.style.background="transparent")}><div style={{width:8,height:8,borderRadius:"50%",background:st.dot,flexShrink:0}}/><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:11,color:"#374151",flex:1}}>{svc.name}</span>{svc.latency!=="—"&&<span style={{fontFamily:"JetBrains Mono, monospace",fontSize:10,color:"#9CA3AF"}}>{svc.latency}</span>}<span style={{fontFamily:"JetBrains Mono, monospace",fontSize:9,fontWeight:600,background:st.bg,color:st.text,padding:"2px 6px",borderRadius:4}}>{st.label}</span></div>;})}</div>
        </div>
        <div style={{background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:12,overflow:"hidden"}}>
          <div style={{padding:"14px 18px",borderBottom:"1px solid #E4E5E9",display:"flex",justifyContent:"space-between",alignItems:"center"}}><span style={{fontSize:13,fontWeight:700,color:"#101114"}}>Alert Feed</span><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:10,color:"#2F5FF6",background:"#EEF2FF",padding:"2px 7px",borderRadius:5}}>AI dedup active</span></div>
          <div>{ALL_ALERTS.slice(0,5).map((a,i)=><div key={i} style={{display:"flex",alignItems:"flex-start",gap:10,padding:"10px 18px",borderBottom:i<4?"1px solid #E4E5E9":"none",background:i===0?"#FFFBF9":"transparent",transition:"background 0.1s"}} onMouseEnter={e=>(e.currentTarget.style.background="#FAFBFF")} onMouseLeave={e=>(e.currentTarget.style.background=i===0?"#FFFBF9":"transparent")}><div style={{width:7,height:7,borderRadius:"50%",background:ALERT_DOT[a.level],flexShrink:0,marginTop:4}}/><div style={{flex:1,minWidth:0}}><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:10,color:"#6B7280"}}>{a.service}</span><div style={{fontSize:12,color:"#374151",marginTop:2}}>{a.msg}</div></div><span style={{fontFamily:"JetBrains Mono, monospace",fontSize:10,color:"#9CA3AF",flexShrink:0}}>{a.time}</span></div>)}</div>
        </div>
      </div>
    </>
  );
}

export default function Dashboard({ onNavigate }: { onNavigate: (p: Page) => void }) {
  const [activeSection, setActiveSection] = useState<SidebarSection>("overview");
  const [notifOpen, setNotifOpen] = useState(false);
  return (
    <div style={{display:"flex",height:"100vh",background:"#F7F8FA",overflow:"hidden"}}>
      <aside style={{width:220,flexShrink:0,background:"#FFFFFF",borderRight:"1px solid #E4E5E9",display:"flex",flexDirection:"column"}}>
        <div style={{padding:"16px 16px 12px",borderBottom:"1px solid #E4E5E9"}}>
          <button onClick={()=>onNavigate("landing")} style={{display:"flex",alignItems:"center",gap:8,background:"none",border:"none",cursor:"pointer",padding:0}}>
            <div style={{width:26,height:26,borderRadius:6,background:"#2F5FF6",display:"flex",alignItems:"center",justifyContent:"center"}}><svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M3 8a5 5 0 0 1 10 0" stroke="white" strokeWidth="1.5" strokeLinecap="round"/><circle cx="8" cy="8" r="2" fill="white"/><path d="M8 6V3M5 7.2 2.8 5M11 7.2 13.2 5" stroke="white" strokeWidth="1.25" strokeLinecap="round"/></svg></div>
            <span style={{fontSize:14,fontWeight:700,color:"#101114",letterSpacing:"-0.02em"}}>IntelliOps</span>
          </button>
        </div>
        <div style={{padding:"10px 12px",borderBottom:"1px solid #E4E5E9"}}>
          <div style={{display:"flex",alignItems:"center",gap:8,padding:"6px 10px",background:"#F7F8FA",borderRadius:7,border:"1px solid #E4E5E9",cursor:"pointer"}}>
            <div style={{width:20,height:20,borderRadius:5,background:"#2F5FF6",display:"flex",alignItems:"center",justifyContent:"center"}}><span style={{fontSize:9,fontWeight:800,color:"#fff"}}>A</span></div>
            <div style={{flex:1,minWidth:0}}><div style={{fontSize:12,fontWeight:600,color:"#101114",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>Acme Corp</div><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:9,color:"#9CA3AF"}}>production</div></div>
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 4l3 3 3-3" stroke="#9CA3AF" strokeWidth="1.25" strokeLinecap="round"/></svg>
          </div>
        </div>
        <nav style={{flex:1,padding:"8px 10px",overflowY:"auto"}}>
          {SIDEBAR_ITEMS.map(item=>{
            const active=activeSection===item.id;
            return <button key={item.id} onClick={()=>{if(item.id==="audit-log"){onNavigate("audit-log");return;}setActiveSection(item.id);}} style={{width:"100%",display:"flex",alignItems:"center",gap:9,padding:"7px 10px",borderRadius:7,marginBottom:2,background:active?"#EEF2FF":"transparent",color:active?"#2F5FF6":"#6B7280",border:"none",cursor:"pointer",textAlign:"left",fontSize:13,fontWeight:active?600:500,transition:"background 0.12s, color 0.12s"}} onMouseEnter={e=>{if(!active){(e.currentTarget as HTMLElement).style.background="#F7F8FA";(e.currentTarget as HTMLElement).style.color="#374151";}}} onMouseLeave={e=>{if(!active){(e.currentTarget as HTMLElement).style.background="transparent";(e.currentTarget as HTMLElement).style.color="#6B7280";}}}><span style={{flexShrink:0}}>{item.icon}</span><span style={{flex:1}}>{item.label}</span>{item.badge!=null&&<span style={{fontFamily:"JetBrains Mono, monospace",fontSize:10,fontWeight:700,background:"#F3F4F6",color:"#6B7280",borderRadius:5,padding:"1px 5px"}}>{item.badge}</span>}</button>;
          })}
        </nav>
        <div style={{borderTop:"1px solid #E4E5E9",padding:"10px 10px"}}>
          <button style={{width:"100%",display:"flex",alignItems:"center",gap:9,padding:"7px 10px",borderRadius:7,marginBottom:8,background:"transparent",color:"#6B7280",border:"none",cursor:"pointer",fontSize:13,fontWeight:500,textAlign:"left"}}><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="2.5" stroke="currentColor" strokeWidth="1.25"/><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1.05 1.05M11.55 11.55l1.05 1.05M3.4 12.6l1.05-1.05M11.55 4.45l1.05-1.05" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round"/></svg>Settings</button>
          <div style={{display:"flex",alignItems:"center",gap:9,padding:"6px 10px"}}><div style={{width:28,height:28,borderRadius:"50%",background:"#2F5FF6",display:"flex",alignItems:"center",justifyContent:"center",flexShrink:0}}><span style={{fontSize:11,fontWeight:700,color:"#fff"}}>MC</span></div><div style={{flex:1,minWidth:0}}><div style={{fontSize:12,fontWeight:600,color:"#101114",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>Maya Chen</div><div style={{fontFamily:"JetBrains Mono, monospace",fontSize:9,color:"#9CA3AF"}}>SRE Lead</div></div></div>
        </div>
      </aside>
      <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
        <header style={{height:52,borderBottom:"1px solid #E4E5E9",background:"#FFFFFF",display:"flex",alignItems:"center",justifyContent:"space-between",padding:"0 24px",flexShrink:0}}>
          <div style={{display:"flex",alignItems:"center",gap:6}}><span style={{fontSize:13,color:"#9CA3AF"}}>IntelliOps</span><span style={{color:"#E4E5E9"}}>/</span><span style={{fontSize:13,fontWeight:600,color:"#101114"}}>{SIDEBAR_ITEMS.find(s=>s.id===activeSection)?.label??"Overview"}</span></div>
          <div style={{display:"flex",alignItems:"center",gap:8}}>
            <div style={{display:"flex",alignItems:"center",gap:8,background:"#F7F8FA",border:"1px solid #E4E5E9",borderRadius:7,padding:"5px 10px",cursor:"text"}}><svg width="13" height="13" viewBox="0 0 13 13" fill="none"><circle cx="5.5" cy="5.5" r="4" stroke="#9CA3AF" strokeWidth="1.2"/><path d="m9 9 2.5 2.5" stroke="#9CA3AF" strokeWidth="1.2" strokeLinecap="round"/></svg><span style={{fontSize:12,color:"#9CA3AF",fontFamily:"JetBrains Mono, monospace"}}>⌘K</span></div>
            <div style={{position:"relative"}}>
              <button onClick={()=>setNotifOpen(x=>!x)} style={{width:34,height:34,borderRadius:8,border:"1px solid #E4E5E9",background:"#FFFFFF",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",color:"#6B7280",position:"relative"}}><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 2a4.5 4.5 0 0 0-4.5 4.5v2.25L2 11h12l-1.5-2.25V6.5A4.5 4.5 0 0 0 8 2Z" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round"/><path d="M6.5 12a1.5 1.5 0 0 0 3 0" stroke="currentColor" strokeWidth="1.25"/></svg><div style={{position:"absolute",top:5,right:5,width:7,height:7,borderRadius:"50%",background:"#EF4444",border:"2px solid #fff"}}/></button>
              {notifOpen&&<div style={{position:"absolute",top:"calc(100% + 6px)",right:0,width:300,background:"#FFFFFF",border:"1px solid #E4E5E9",borderRadius:10,boxShadow:"0 8px 24px rgba(16,17,20,0.1)",zIndex:50,overflow:"hidden"}}><div style={{padding:"12px 14px",borderBottom:"1px solid #E4E5E9",fontSize:12,fontWeight:700,color:"#101114"}}>Notifications</div><div style={{padding:"16px 14px",fontFamily:"JetBrains Mono, monospace",fontSize:11,color:"#9CA3AF",textAlign:"center"}}>No new notifications</div></div>}
            </div>
            <div style={{width:32,height:32,borderRadius:"50%",background:"#2F5FF6",display:"flex",alignItems:"center",justifyContent:"center",cursor:"pointer"}}><span style={{fontSize:11,fontWeight:700,color:"#fff"}}>MC</span></div>
          </div>
        </header>
        <main style={{flex:1,overflowY:"auto",padding:"24px 28px"}}>
          {activeSection==="overview"&&<OverviewTab/>}
          {activeSection==="alerts"&&<AlertsTab/>}
          {activeSection==="services"&&<ServicesTab/>}
          {activeSection==="metrics"&&<MetricsTab/>}
        </main>
      </div>
    </div>
  );
}
