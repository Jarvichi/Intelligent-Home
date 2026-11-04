const base=location.pathname.endsWith('/')?location.pathname:location.pathname+'/';
const el=(tag,text)=>{const n=document.createElement(tag);if(text!=null)n.textContent=String(text);return n};
function date(value){if(!value)return 'Not reported';const d=new Date(value);return Number.isNaN(d.getTime())?'Not reported':d.toLocaleString()}
let busy=false;
async function refresh(){
 if(busy)return;busy=true;
 const button=document.querySelector('#check');button.disabled=true;
 await Promise.allSettled([service(),jobs()]);
 button.disabled=false;busy=false;
}
async function service(){
 const s=document.querySelector('#status'),d=document.querySelector('#dot'),u=document.querySelector('#updated'),badge=document.querySelector('#refresh');
 try{const r=await fetch(base+'api/status',{cache:'no-store'});if(!r.ok)throw Error();const j=await r.json();s.textContent=j.healthy?'Available':'Unavailable';d.className='ih-status-dot '+(j.healthy?'ok':'bad');u.textContent='Checked '+date(j.checked_at*1000);badge.textContent=j.healthy?'Service available':'Service unavailable';badge.setAttribute('status',j.healthy?'ok':'error')}
 catch(e){s.textContent='Status unavailable';d.className='ih-status-dot bad';u.textContent='Could not reach the add-on status endpoint';badge.textContent='Service unavailable';badge.setAttribute('status','error')}
}
async function jobs(){
 const target=document.querySelector('#job-results'),status=document.querySelector('#jobs-updated');
 target.setAttribute('aria-busy','true');
 try{const r=await fetch(base+'api/jobs',{cache:'no-store'});if(!r.ok)throw Error();const j=await r.json();if(!j.available||!Array.isArray(j.jobs))throw Error();
 const cards=j.jobs.map(job=>{const card=el('ih-card');card.append(el('p',job.kind),el('h3',job.name));const facts=el('dl');
 const last=job.last_run?date(job.last_run):job.last_run_s==null?'Not reported':Math.max(0,job.last_run_s)+' seconds before check';
 const outcome=job.last_ok===true?'Succeeded':job.last_ok===false?'Failed':'Not reported';
 [['Mode',job.mode||'Unknown'],['Last run',last],['Last run mode',job.last_mode||'Not reported'],['Outcome',outcome],['Next run',date(job.next_run)],['Runs',job.runs??'Not reported'],['Errors',job.errors??'Not reported']].forEach(([label,value])=>facts.append(el('dt',label),el('dd',value)));
 card.append(facts);if(job.last_error)card.append(el('p','Last error: '+job.last_error));if(job.why)card.append(el('p',job.why));return card});
 target.replaceChildren(...cards);if(!cards.length)target.append(el('ih-empty','No jobs or plugins are reported. Review the add-on configuration.'));
 status.textContent='Results checked '+date(j.checked_at*1000);
 }catch(e){target.replaceChildren(el('ih-error','Job results unavailable. Check the service and try again.'));status.textContent='Could not refresh job results.'}
 finally{target.setAttribute('aria-busy','false')}
}
document.querySelector('#check').addEventListener('click',refresh);refresh();setInterval(()=>{if(!document.hidden)refresh()},30000);
