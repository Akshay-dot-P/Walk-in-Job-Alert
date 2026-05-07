cat > /home/claude/jobs_page.html << 'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Walk-In Jobs · Bangalore</title>
<style>
/* ─── TOKENS ─────────────────────────────────────────────────────────────────
   Apple HIG: Clarity · Deference · Depth
   Font stack gives SF Pro on Apple devices, Segoe UI on Windows,
   falls back to system-ui everywhere else.
   8-pt spatial grid. Minimal palette. Typography does the heavy lifting.
────────────────────────────────────────────────────────────────────────────── */
:root{
  --bg:       #111113;
  --surface:  #1c1c1e;
  --surface2: #242426;
  --surface3: #2c2c2e;
  --sep:      rgba(255,255,255,.08);
  --sep2:     rgba(255,255,255,.05);

  --t1: #f5f5f7;
  --t2: #a1a1a6;
  --t3: #636366;
  --t4: #48484a;

  --green:  #30d158;
  --amber:  #ff9f0a;
  --red:    #ff453a;
  --blue:   #0a84ff;

  --ease: cubic-bezier(.4,0,.2,1);
  --r: 14px;
  --r-sm: 8px;
}

*,*::before,*::after{margin:0;padding:0;box-sizing:border-box;}
html{font-size:15px;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;}
body{
  font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',system-ui,sans-serif;
  background:var(--bg);
  color:var(--t1);
  min-height:100vh;
}

/* ─── NAVBAR ─────────────────────────────────────────────────────────────── */
.navbar{
  position:sticky;top:0;z-index:100;
  background:rgba(17,17,19,.9);
  backdrop-filter:saturate(180%) blur(20px);
  -webkit-backdrop-filter:saturate(180%) blur(20px);
  border-bottom:1px solid var(--sep2);
}
.nav-inner{
  max-width:860px;margin:0 auto;padding:0 20px;
  height:52px;display:flex;align-items:center;gap:14px;
}
.nav-logo{
  font-size:.85rem;font-weight:700;
  letter-spacing:-.025em;color:var(--t1);
  white-space:nowrap;flex-shrink:0;
}
.nav-logo span{color:var(--blue);}

.search-box{
  flex:1;max-width:320px;
  position:relative;
}
.search-box svg{
  position:absolute;left:10px;top:50%;transform:translateY(-50%);
  width:13px;height:13px;color:var(--t3);pointer-events:none;
}
#q{
  width:100%;padding:7px 12px 7px 30px;
  background:var(--surface2);
  border:1px solid var(--sep);
  border-radius:10px;
  font-family:inherit;font-size:.8rem;color:var(--t1);
  outline:none;
  transition:border-color .18s;
}
#q::placeholder{color:var(--t4);}
#q:focus{border-color:rgba(10,132,255,.5);}

.nav-right{margin-left:auto;display:flex;align-items:center;gap:14px;flex-shrink:0;}
.nav-stat{text-align:right;}
.nav-stat .nv{font-size:.9rem;font-weight:700;letter-spacing:-.02em;color:var(--t1);font-variant-numeric:tabular-nums;}
.nav-stat .nl{font-size:.58rem;font-weight:600;color:var(--t3);text-transform:uppercase;letter-spacing:.08em;}
.sort-sel{
  background:transparent;
  border:1px solid var(--sep);
  border-radius:var(--r-sm);
  color:var(--t2);
  font-family:inherit;font-size:.72rem;font-weight:500;
  padding:5px 8px;cursor:pointer;outline:none;
}
.sort-sel option{background:#1c1c1e;}
.sort-sel:focus{border-color:rgba(10,132,255,.5);}

/* ─── FILTER RAIL ────────────────────────────────────────────────────────── */
.filter-rail{
  border-bottom:1px solid var(--sep2);
  background:rgba(17,17,19,.85);
  backdrop-filter:blur(12px);
}
.filter-inner{
  max-width:860px;margin:0 auto;padding:0 20px;
  display:flex;align-items:center;gap:6px;
  height:42px;
  overflow-x:auto;scrollbar-width:none;
}
.filter-inner::-webkit-scrollbar{display:none;}
.fg{font-size:.6rem;font-weight:700;color:var(--t4);text-transform:uppercase;letter-spacing:.1em;white-space:nowrap;flex-shrink:0;margin-right:1px;}
.fbar{width:1px;height:14px;background:var(--sep);flex-shrink:0;margin:0 3px;}

.chip{
  display:inline-flex;align-items:center;gap:4px;
  padding:4px 10px;
  border-radius:999px;
  font-family:inherit;font-size:.68rem;font-weight:500;
  border:1.5px solid transparent;
  background:transparent;color:var(--t3);
  cursor:pointer;white-space:nowrap;flex-shrink:0;
  transition:all .14s var(--ease);
  outline:none;-webkit-tap-highlight-color:transparent;
}
.chip:hover{color:var(--t1);background:var(--sep2);}
.chip.on{
  background:var(--surface2);
  border-color:var(--sep);
  color:var(--t1);font-weight:600;
  box-shadow:0 1px 4px rgba(0,0,0,.3);
}
.chip .pip{width:6px;height:6px;border-radius:50%;flex-shrink:0;}

/* ─── PAGE ───────────────────────────────────────────────────────────────── */
.page{max-width:860px;margin:0 auto;padding:20px 20px 64px;}

/* ─── UPDATED BAR ────────────────────────────────────────────────────────── */
.updated-bar{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:20px;
}
.updated-bar .ul{font-size:.72rem;color:var(--t3);}
.updated-bar .ul strong{color:var(--t2);font-weight:600;}
.res-count{font-size:.72rem;color:var(--t3);font-variant-numeric:tabular-nums;}
.res-count strong{color:var(--t1);font-weight:700;}

/* ─── SECTION HEADERS ────────────────────────────────────────────────────── */
.section-head{
  padding:18px 0 10px;
  border-bottom:1px solid var(--sep2);
  margin-bottom:12px;
}
.section-head:not(:first-child){margin-top:28px;}
.sh-label{
  font-size:.68rem;font-weight:700;
  letter-spacing:.1em;text-transform:uppercase;
  color:var(--t3);
}
.sh-sub{font-size:.68rem;color:var(--t4);font-weight:400;}

/* ─── CARD ───────────────────────────────────────────────────────────────── */
.card{
  background:var(--surface);
  border-radius:var(--r);
  border:1px solid var(--sep2);
  padding:18px 20px;
  margin-bottom:8px;
  display:flex;gap:16px;align-items:flex-start;
  animation:up .28s var(--ease) both;
  transition:background .15s,border-color .15s,box-shadow .15s;
}
.card:hover{background:var(--surface2);border-color:var(--sep);box-shadow:0 4px 20px rgba(0,0,0,.25);}
@keyframes up{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:translateY(0);}}

/* Card left */
.card-left{flex:1;min-width:0;display:flex;flex-direction:column;gap:7px;}

/* Title row */
.title-row{display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap;}
.card-title{
  font-size:1rem;font-weight:700;
  letter-spacing:-.025em;line-height:1.3;
  color:var(--t1);
}
.new-badge{
  display:inline-flex;align-items:center;
  padding:2px 7px;border-radius:999px;
  font-size:.6rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  background:rgba(10,132,255,.12);
  color:var(--blue);
  border:1px solid rgba(10,132,255,.2);
  flex-shrink:0;margin-top:2px;
  white-space:nowrap;
}

/* Company row */
.co-row{
  font-size:.78rem;color:var(--t2);font-weight:400;
  display:flex;align-items:center;gap:4px;flex-wrap:wrap;
}
.co-sep{color:var(--t4);}
.co-tier{
  font-size:.62rem;font-weight:600;
  padding:2px 6px;border-radius:4px;
  border:1px solid transparent;
}
.co-mnc    {color:#0a84ff;background:rgba(10,132,255,.08);border-color:rgba(10,132,255,.15);}
.co-startup{color:var(--green);background:rgba(48,209,88,.08);border-color:rgba(48,209,88,.15);}
.co-mid    {color:var(--amber);background:rgba(255,159,10,.08);border-color:rgba(255,159,10,.15);}

/* Tags */
.tag-row{display:flex;flex-wrap:wrap;gap:5px;}
.tag{
  display:inline-flex;align-items:center;gap:3px;
  padding:3px 9px;border-radius:999px;
  font-size:.66rem;font-weight:600;
  border:1px solid transparent;
  white-space:nowrap;
}

/* domain tag colors */
.tag-SOC      {color:#0a84ff;background:rgba(10,132,255,.1);border-color:rgba(10,132,255,.2);}
.tag-GRC      {color:#7d7aff;background:rgba(125,122,255,.1);border-color:rgba(125,122,255,.2);}
.tag-AppSec   {color:var(--green);background:rgba(48,209,88,.1);border-color:rgba(48,209,88,.2);}
.tag-VAPT     {color:#ff6b35;background:rgba(255,107,53,.1);border-color:rgba(255,107,53,.2);}
.tag-CloudSec {color:#32ade6;background:rgba(50,173,230,.1);border-color:rgba(50,173,230,.2);}
.tag-IAM      {color:#ff375f;background:rgba(255,55,95,.1);border-color:rgba(255,55,95,.2);}
.tag-Risk     {color:var(--amber);background:rgba(255,159,10,.1);border-color:rgba(255,159,10,.2);}
.tag-FraudAML {color:#bf5af2;background:rgba(191,90,242,.1);border-color:rgba(191,90,242,.2);}
.tag-Forensics{color:#00c7be;background:rgba(0,199,190,.1);border-color:rgba(0,199,190,.2);}
.tag-General  {color:var(--t2);background:var(--surface3);border-color:var(--sep);}
.tag-exp      {color:var(--t3);background:var(--surface3);border-color:var(--sep);}

/* Summary */
.card-summary{
  font-size:.78rem;color:var(--t3);line-height:1.6;font-weight:400;
}

/* Actions */
.card-actions{display:flex;gap:6px;flex-wrap:wrap;padding-top:2px;}
.btn-apply{
  display:inline-flex;align-items:center;gap:5px;
  padding:7px 14px;border-radius:var(--r-sm);
  font-family:inherit;font-size:.73rem;font-weight:600;
  text-decoration:none;background:var(--blue);color:#fff;
  border:none;cursor:pointer;letter-spacing:-.01em;
  transition:background .14s,box-shadow .14s,transform .1s;
  box-shadow:0 1px 5px rgba(10,132,255,.3);
}
.btn-apply:hover{background:#0070e8;box-shadow:0 2px 10px rgba(10,132,255,.4);transform:translateY(-1px);}
.btn-apply:active{transform:none;}
.btn-apply svg{width:11px;height:11px;flex-shrink:0;}
.btn-sm{
  display:inline-flex;align-items:center;gap:4px;
  padding:7px 11px;border-radius:var(--r-sm);
  font-family:inherit;font-size:.7rem;font-weight:600;
  text-decoration:none;background:var(--surface3);color:var(--t2);
  border:1px solid var(--sep);cursor:pointer;letter-spacing:-.01em;
  transition:all .14s var(--ease);
}
.btn-sm:hover{color:var(--t1);border-color:rgba(255,255,255,.15);background:var(--surface3);}
.btn-sm svg{width:10px;height:10px;flex-shrink:0;}

/* ─── SCORE PANEL (right side of card) ───────────────────────────────────── */
.card-right{
  display:flex;flex-direction:column;align-items:flex-end;gap:4px;
  flex-shrink:0;min-width:80px;padding-top:2px;
}
.odds-pct{
  font-size:1.55rem;font-weight:800;
  letter-spacing:-.04em;line-height:1;
  font-variant-numeric:tabular-nums;
}
.odds-pct.hi {color:var(--green);}
.odds-pct.med{color:var(--amber);}
.odds-pct.lo {color:var(--red);}
.odds-label{
  font-size:.6rem;font-weight:600;color:var(--t3);
  text-align:right;letter-spacing:.02em;line-height:1.4;
}
.stars{
  display:flex;gap:2px;margin-top:2px;
}
.star{
  width:11px;height:11px;
  clip-path:polygon(50% 0%,61% 35%,98% 35%,68% 57%,79% 91%,50% 70%,21% 91%,32% 57%,2% 35%,39% 35%);
}
.star.on{background:var(--amber);}
.star.off{background:var(--surface3);}

/* ─── EMPTY STATE ────────────────────────────────────────────────────────── */
#empty{
  display:none;padding:80px 20px;text-align:center;
}
.ei{font-size:2rem;margin-bottom:10px;}
.et{font-size:.95rem;font-weight:600;color:var(--t1);letter-spacing:-.02em;margin-bottom:4px;}
.es{font-size:.78rem;color:var(--t3);}

/* ─── SCROLLBAR ──────────────────────────────────────────────────────────── */
::-webkit-scrollbar{width:5px;}
::-webkit-scrollbar-track{background:transparent;}
::-webkit-scrollbar-thumb{background:var(--surface3);border-radius:3px;}

/* ─── RESPONSIVE ─────────────────────────────────────────────────────────── */
@media(max-width:600px){
  .nav-right .nav-stat:not(:first-child){display:none;}
  .card{flex-direction:column;gap:12px;}
  .card-right{flex-direction:row;align-items:center;justify-content:flex-start;min-width:auto;gap:14px;}
  .odds-pct{font-size:1.2rem;}
  .odds-label{display:none;}
}
</style>
</head>
<body>

<!-- ── NAVBAR ────────────────────────────────────────────────────────────── -->
<nav class="navbar">
  <div class="nav-inner">
    <div class="nav-logo">CyberJobs<span> BLR</span></div>

    <div class="search-box">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
      <input id="q" type="text" placeholder="Role, company…" autocomplete="off" spellcheck="false">
    </div>

    <div class="nav-right">
      <div class="nav-stat">
        <div class="nv" id="totalCount">—</div>
        <div class="nl">Jobs</div>
      </div>
      <div class="nav-stat">
        <div class="nv" id="newCount">—</div>
        <div class="nl">New today</div>
      </div>
      <select class="sort-sel" id="sortSel">
        <option value="default">Relevance</option>
        <option value="newest">Newest first</option>
        <option value="hire_desc">Hireability ↓</option>
        <option value="cred_desc">Credibility ↓</option>
        <option value="date_desc">Date posted ↓</option>
        <option value="az">Title A → Z</option>
        <option value="za">Title Z → A</option>
      </select>
    </div>
  </div>
</nav>

<!-- ── FILTER RAIL ────────────────────────────────────────────────────────── -->
<div class="filter-rail">
  <div class="filter-inner">
    <span class="fg">Domain</span>
    <button class="chip on" data-f="domain" data-v="all">All</button>
    <button class="chip" data-f="domain" data-v="SOC"><span class="pip" style="background:#0a84ff"></span>SOC</button>
    <button class="chip" data-f="domain" data-v="GRC"><span class="pip" style="background:#7d7aff"></span>GRC</button>
    <button class="chip" data-f="domain" data-v="AppSec"><span class="pip" style="background:#30d158"></span>AppSec</button>
    <button class="chip" data-f="domain" data-v="VAPT"><span class="pip" style="background:#ff6b35"></span>VAPT</button>
    <button class="chip" data-f="domain" data-v="CloudSec"><span class="pip" style="background:#32ade6"></span>CloudSec</button>
    <button class="chip" data-f="domain" data-v="IAM"><span class="pip" style="background:#ff375f"></span>IAM</button>
    <button class="chip" data-f="domain" data-v="Risk"><span class="pip" style="background:#ff9f0a"></span>Risk</button>
    <button class="chip" data-f="domain" data-v="Fraud-AML"><span class="pip" style="background:#bf5af2"></span>Fraud</button>
    <button class="chip" data-f="domain" data-v="Forensics"><span class="pip" style="background:#00c7be"></span>Forensics</button>
    <button class="chip" data-f="domain" data-v="General"><span class="pip" style="background:#636366"></span>General</button>
    <div class="fbar"></div>
    <span class="fg">Tier</span>
    <button class="chip on" data-f="tier" data-v="all">All</button>
    <button class="chip" data-f="tier" data-v="MNC">MNC</button>
    <button class="chip" data-f="tier" data-v="startup">Startup</button>
    <button class="chip" data-f="tier" data-v="mid-tier">Mid-tier</button>
  </div>
</div>

<!-- ── PAGE ───────────────────────────────────────────────────────────────── -->
<main class="page">
  <div class="updated-bar">
    <span class="ul">Last updated <strong id="updatedAt">—</strong></span>
    <span class="res-count"><strong id="shownCount">—</strong> listings shown</span>
  </div>
  <div id="feed"></div>
  <div id="empty">
    <div class="ei">🔍</div>
    <p class="et">No listings match</p>
    <p class="es">Adjust your filters or clear search.</p>
  </div>
</main>

<script>
/*JOBS_PLACEHOLDER*/

/* ─── ENRICH JOBS ON LOAD ───────────────────────────────────────────────── */
// Tag every job with its original array position.
// Higher _idx = more recently appended to the sheet = "newer".
// This is the reliable recency proxy when scraped_at/posted_date are missing.
JOBS.forEach((j, i) => { j._idx = i; });

// Mark jobs in the top 20% of _idx as "new" (most recent batch)
const newCutoff = JOBS.length > 0 ? JOBS[Math.floor(JOBS.length * 0.80)]._idx : Infinity;

// Record generation date
const GEN_DATE = new Date().toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'});

/* ─── HELPERS ───────────────────────────────────────────────────────────── */
function getTier(c){
  const s=(c||'').toLowerCase();
  if(s.includes('(mnc)'))      return 'MNC';
  if(s.includes('(startup)'))  return 'startup';
  if(s.includes('(mid-tier)')) return 'mid-tier';
  return 'unknown';
}
function stripTier(c){ return (c||'').replace(/\s*\(.*?\)\s*$/, '').trim(); }

function oddsClass(h){
  const p = h != null ? Math.round((h/7)*100) : 0;
  return p >= 70 ? 'hi' : p >= 50 ? 'med' : 'lo';
}

function starHTML(cred, max=6){
  const filled = cred != null ? Math.round((cred/max)*5) : 0;
  let h = '';
  for(let i=1;i<=5;i++) h+=`<span class="star ${i<=filled?'on':'off'}"></span>`;
  return h;
}

const TIER_CLS = {MNC:'co-mnc', startup:'co-startup', 'mid-tier':'co-mid'};
const TIER_LABEL = {MNC:'MNC', startup:'Startup', 'mid-tier':'Mid-tier'};
const DOM_CLS = (d) => `tag-${(d||'General').replace('-','')}`;

const DOC_ICO = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
const EXT_ICO = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M10 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>`;

function buildCard(j, animIdx){
  const tier  = getTier(j.company);
  const name  = stripTier(j.company);
  const isNew = j._idx >= newCutoff;
  const hire  = j.hireability;
  const cred  = j.credibility;
  const pct   = hire != null ? Math.round((hire/7)*100) : null;
  const oc    = oddsClass(hire);
  const tierCls = TIER_CLS[tier] || '';
  const tierLbl = TIER_LABEL[tier] || '';

  const tags = [
    `<span class="tag ${DOM_CLS(j.domain)}">${j.domain}</span>`,
    j.experience_required && j.experience_required !== 'null'
      ? `<span class="tag tag-exp">${j.experience_required}</span>` : ''
  ].filter(Boolean).join('');

  const scorePanel = hire != null ? `
    <div class="card-right">
      <div class="odds-pct ${oc}">${pct}%</div>
      <div class="odds-label">interview<br>odds</div>
      <div class="stars">${starHTML(cred)}</div>
    </div>` : '';

  return `
<div class="card" style="animation-delay:${Math.min(animIdx,30)*.025}s">
  <div class="card-left">
    <div class="title-row">
      <span class="card-title">${j.job_title}${name ? ' — '+name : ''}</span>
      ${isNew ? '<span class="new-badge">New</span>' : ''}
    </div>
    <div class="co-row">
      ${tierCls ? `<span class="co-tier ${tierCls}">${tierLbl}</span><span class="co-sep">·</span>` : ''}
      <span>Bengaluru</span>
      <span class="co-sep">·</span>
      <span>Full-time</span>
      ${j.salary_range ? `<span class="co-sep">·</span><span>${j.salary_range}</span>` : ''}
      ${j.posted_date ? `<span class="co-sep">·</span><span>${j.posted_date.slice(0,10)}</span>` : ''}
    </div>
    <div class="tag-row">${tags}</div>
    ${j.summary ? `<div class="card-summary">${j.summary}</div>` : ''}
    <div class="card-actions">
      <a href="${j.apply_url}" target="_blank" rel="noopener" class="btn-apply">${EXT_ICO} Apply</a>
      ${j.resume_doc_link ? `<a href="${j.resume_doc_link}" target="_blank" rel="noopener" class="btn-sm">${DOC_ICO} Resume .doc</a>` : ''}
      ${j.resume_pdf_link ? `<a href="${j.resume_pdf_link}" target="_blank" rel="noopener" class="btn-sm">${DOC_ICO} .pdf</a>` : ''}
    </div>
  </div>
  ${scorePanel}
</div>`;
}

/* ─── SECTIONS ──────────────────────────────────────────────────────────── */
const SECTIONS = [
  { id:'top',   label:'Top Matches',       sub:'— Excellent fit', test: j => (j.hireability||0) >= 5.5 },
  { id:'strong',label:'Strong Matches',    sub:'— Good fit, some gaps', test: j => (j.hireability||0) >= 4 && (j.hireability||0) < 5.5 },
  { id:'maybe', label:'Potential Matches', sub:'— Worth exploring', test: j => (j.hireability||0) >= 2.5 && (j.hireability||0) < 4 },
  { id:'other', label:'Other Listings',    sub:'', test: () => true },
];

/* ─── STATE ─────────────────────────────────────────────────────────────── */
let domF='all', tierF='all', searchQ='', sortBy='default';

function applySort(arr){
  const a = [...arr];
  switch(sortBy){
    case 'newest':     return a.sort((x,y) => y._idx - x._idx);
    case 'hire_desc':  return a.sort((x,y) => (y.hireability||0) - (x.hireability||0));
    case 'cred_desc':  return a.sort((x,y) => (y.credibility||0) - (x.credibility||0));
    case 'date_desc': {
      // Use posted_date if available, fall back to _idx (newer=higher index)
      return a.sort((x,y) => {
        const pd = (y.posted_date||'').localeCompare(x.posted_date||'');
        return pd !== 0 ? pd : y._idx - x._idx;
      });
    }
    case 'az': return a.sort((x,y) => (x.job_title||'').localeCompare(y.job_title||''));
    case 'za': return a.sort((x,y) => (y.job_title||'').localeCompare(x.job_title||''));
    default:   return a.sort((x,y) => y._idx - x._idx); // newest first by default
  }
}

function getFiltered(){
  return JOBS.filter(j => {
    if(domF !== 'all' && j.domain !== domF) return false;
    if(tierF !== 'all' && getTier(j.company) !== tierF) return false;
    if(searchQ){
      const q = searchQ.toLowerCase();
      if(!(j.job_title||'').toLowerCase().includes(q) &&
         !(j.company||'').toLowerCase().includes(q) &&
         !(j.summary||'').toLowerCase().includes(q)) return false;
    }
    return true;
  });
}

function render(){
  const raw     = getFiltered();
  const sorted  = applySort(raw);
  const feed    = document.getElementById('feed');
  const empty   = document.getElementById('empty');

  document.getElementById('shownCount').textContent = sorted.length;

  if(!sorted.length){
    feed.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  // When a non-default sort is active, render flat (no sections)
  if(sortBy !== 'default'){
    let html = '', animI = 0;
    sorted.forEach(j => { html += buildCard(j, animI++); });
    feed.innerHTML = html;
    return;
  }

  // Default: group into sections (each section sorted newest-first internally)
  let html = '', animI = 0;
  let remaining = [...sorted];

  SECTIONS.forEach(sec => {
    const group = remaining.filter(sec.test);
    remaining   = remaining.filter(j => !sec.test(j));
    if(!group.length) return;

    // Within each section, newest first
    const gs = [...group].sort((a,b) => b._idx - a._idx);

    html += `<div class="section-head">
      <span class="sh-label">${sec.label}</span>
      ${sec.sub ? `<span class="sh-sub"> ${sec.sub}</span>` : ''}
    </div>`;
    gs.forEach(j => { html += buildCard(j, animI++); });
  });

  feed.innerHTML = html;
}

/* ─── INIT ──────────────────────────────────────────────────────────────── */
document.getElementById('totalCount').textContent = JOBS.length;
document.getElementById('newCount').textContent   = JOBS.filter(j => j._idx >= newCutoff).length;
document.getElementById('updatedAt').textContent  = GEN_DATE;

/* ─── EVENTS ────────────────────────────────────────────────────────────── */
// Domain filter chips
document.querySelectorAll('.chip[data-f="domain"]').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.chip[data-f="domain"]').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    domF = b.dataset.v;
    render();
  });
});

// Tier filter chips
document.querySelectorAll('.chip[data-f="tier"]').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.chip[data-f="tier"]').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    tierF = b.dataset.v;
    render();
  });
});

// Search
let searchTimer;
document.getElementById('q').addEventListener('input', e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { searchQ = e.target.value.trim(); render(); }, 150);
});

// Sort
document.getElementById('sortSel').addEventListener('change', e => {
  sortBy = e.target.value;
  render();
});

render();
</script>
</body>
</html>
