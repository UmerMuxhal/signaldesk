'use strict';
const $ = id => document.getElementById(id);
let mode = 'sample', selected = null, selectedJob = null, lastRequest = null, jobs = [], metrics = {total:0,by_status:{}}, busy = false, pollFailures = 0;
const drafts = new Map();
let listSignature = '', configured = false, initializing = false;
try { selected = localStorage.getItem('signaldesk-selected'); } catch {}
const labels = {queued:'Queued',running:'Processing',retrying:'Retry scheduled',needs_review:'Needs review',approved:'Approved',failed:'Failed'};
const examples = {
 shipping:'My order 1042 was due yesterday, but tracking has not updated. Can you check the delivery?',
 billing:'I was charged twice for invoice 2087. Please review the duplicate payment before issuing any refund.',
 account:'My account is locked and I cannot sign in. I need help restoring access safely.'
};
function el(tag, cls, text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
function notify(message,error=false){$('notice').textContent=message;$('notice').className=error?'error':'';}
async function api(path,options={}){const response=await fetch(path,{signal:AbortSignal.timeout(10000),...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Check the request fields and try again.');return data;}
function statusBadge(status){return el('span','status '+status,labels[status]||status);}
function timeLabel(value){return new Date(value*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});}
function renderJobs(){
 $('total').textContent=metrics.total;$('review-count').textContent=metrics.by_status.needs_review||0;$('approved-count').textContent=metrics.by_status.approved||0;$('failed-count').textContent=metrics.by_status.failed||0;$('inbox-count').textContent=jobs.length+' latest';
 const signature=JSON.stringify([jobs,selected]);if(signature===listSignature)return;listSignature=signature;
 if(!jobs.length){$('jobs').replaceChildren(el('div','empty','No requests yet. Queue a synthetic example to begin.'));return;}
 const focusedId=document.activeElement?.dataset.jobId;
 const fragment=document.createDocumentFragment();
 for(const j of jobs){const button=el('button','job-item'+(selected===j.id?' selected':''));button.type='button';button.dataset.jobId=j.id;button.setAttribute('aria-pressed',String(selected===j.id));const row=el('div','job-row');row.append(el('span','job-id',j.id.slice(0,8).toUpperCase()),statusBadge(j.status));button.append(row,el('div','job-title',j.payload.message.slice(0,110)+(j.payload.message.length>110?'…':'')),el('div','job-meta',`${timeLabel(j.created)} · ${j.attempt} attempt${j.attempt===1?'':'s'} · ${j.payload.scenario.replaceAll('_',' ')}`));button.addEventListener('click',()=>selectJob(j.id));fragment.append(button);}
 $('jobs').replaceChildren(fragment);
 if(focusedId){const button=[...$('jobs').querySelectorAll('button')].find(b=>b.dataset.jobId===focusedId);button?.focus({preventScroll:true});}
}
function renderDetail(job){
 $('selected-label').textContent=`Request ${job.id.slice(0,8).toUpperCase()} · ${labels[job.status]}`;$('export').disabled=false;
 const grid=el('div','detail-grid'),trace=el('div'),right=el('div');trace.append(el('div','section-label','PROCESSING TRAIL'));
 for(const event of job.events){const item=el('div','event');item.append(el('strong','',event.kind),el('p','',event.detail),el('small','',timeLabel(event.at)));trace.append(item);}
 right.append(el('div','section-label','VALIDATED OUTPUT'));
 const result=job.approved_result||job.result;
 if(result){const meta=el('div','result-meta');meta.append(el('span','',result.category),el('span','',result.priority+' priority'),el('span','',job.status==='approved'?'Reviewed':'Human approval required'));right.append(meta);
 const fields=el('div','review-fields'),summaryLabel=el('label','', 'Summary'),summary=el('textarea'),replyLabel=el('label','','Suggested response'),reply=el('textarea','reply');summary.id='review-summary';summary.maxLength=300;summary.minLength=8;summary.value=(job.status==='needs_review'?drafts.get(job.id)?.summary:undefined)??result.summary;summaryLabel.htmlFor=summary.id;reply.id='review-reply';reply.maxLength=800;reply.minLength=12;reply.value=(job.status==='needs_review'?drafts.get(job.id)?.suggested_reply:undefined)??result.suggested_reply;replyLabel.htmlFor=reply.id;summary.disabled=reply.disabled=job.status==='approved';fields.append(summaryLabel,summary,replyLabel,reply);
 for(const field of [summary,reply])field.addEventListener('input',()=>drafts.set(job.id,{summary:summary.value,suggested_reply:reply.value}));
 if(result.missing_information.length)fields.append(el('p','field-help','Missing information: '+result.missing_information.join('; ')));
 if(job.status==='needs_review'){const approve=el('button','primary','Approve reviewed result');approve.id='approve-result';approve.addEventListener('click',async()=>{if(!summary.reportValidity()||!reply.reportValidity())return;approve.disabled=true;try{const body={result:{...result,summary:summary.value.trim(),suggested_reply:reply.value.trim()}};const approved=await api('/api/jobs/'+job.id+'/approve',{method:'POST',body:JSON.stringify(body)});drafts.delete(job.id);if(selected===job.id){selectedJob=approved;renderDetail(approved);}await refresh();notify('Reviewed result saved. No external action or message was sent.');}catch(e){notify(e.message,true);approve.disabled=false;}});fields.append(approve);}
 else fields.append(el('p','field-help','Approval is recorded. Nothing has been sent to the customer.'));
 right.append(fields);
 const usage=job.usage;right.append(el('div','usage',usage?.mode==='sample'?'Sample rules · API spend $0 · no model request made.':`Live provider: ${usage?.model||'unknown'}. Tokens recorded; monetary cost is not estimated.`));
 }else if(job.status==='failed'){const box=el('div','error-box');box.append(el('strong','',job.error),el('p','','Processing stopped visibly. Invalid output and provider refusals never become approved records. Inspect the event trail before submitting a corrected request.'));right.append(box);}else{right.append(el('div','waiting','Processing is asynchronous. A validated result will appear here when the worker completes. Retries use a bounded backoff.'));}
 grid.append(trace,right);$('detail').replaceChildren(grid);
}
async function selectJob(id){selected=id;try{localStorage.setItem('signaldesk-selected',id);}catch{}selectedJob=null;$('export').disabled=true;$('selected-label').textContent='Loading selected request…';$('detail').replaceChildren(el('div','waiting','Loading request…'));renderJobs();try{const next=await api('/api/jobs/'+id);if(selected!==id)return;selectedJob=next;renderDetail(selectedJob);}catch(e){notify(e.message,true);}}
async function refresh(){
 if(busy)return;busy=true;
 try{[jobs,metrics]=await Promise.all([api('/api/jobs'),api('/api/metrics')]);renderJobs();if(selected){const id=selected,next=await api('/api/jobs/'+id);if(selected===id&&(!selectedJob||next.updated>=selectedJob.updated)){const changed=!selectedJob||next.updated!==selectedJob.updated||next.events.length!==selectedJob.events.length;selectedJob=next;if(changed)renderDetail(next);}}if(pollFailures)notify('Connection restored. Persisted jobs are up to date.');pollFailures=0;}
 catch(e){if(++pollFailures===1)notify('Connection interrupted. Reconnecting automatically; existing jobs are kept in the database.',true);}
 finally{busy=false;}
}
document.querySelectorAll('[data-example]').forEach(button=>button.addEventListener('click',()=>{$('message').value=examples[button.dataset.example];$('message').dispatchEvent(new Event('input'));$('message').focus();}));
$('message').addEventListener('input',()=>{$('character-count').textContent=$('message').value.length+' / 4000';});
$('request-form').addEventListener('submit',async event=>{event.preventDefault();$('submit-button').disabled=true;const payload={message:$('message').value.trim(),scenario:$('scenario').value},key=crypto.randomUUID();lastRequest={payload,key};$('duplicate').disabled=false;try{const result=await api('/api/jobs',{method:'POST',headers:{'Idempotency-Key':key},body:JSON.stringify(payload)});await selectJob(result.id);await refresh();notify('Request queued. The worker will process it in the background.');}catch(e){notify(e.message,true);}finally{$('submit-button').disabled=false;}});
$('duplicate').addEventListener('click',async()=>{if(!lastRequest)return;$('duplicate').disabled=true;try{const result=await api('/api/jobs',{method:'POST',headers:{'Idempotency-Key':lastRequest.key},body:JSON.stringify(lastRequest.payload)});notify(result.deduplicated?'Duplicate prevented: the existing job was returned.':'Request queued.');await selectJob(result.id);await refresh();}catch(e){notify(e.message,true);}finally{$('duplicate').disabled=false;}});
$('export').addEventListener('click',async()=>{if(!selectedJob)return;const id=selected;try{const record=await api('/api/jobs/'+id);const blob=new Blob([JSON.stringify(record,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download='signaldesk-'+record.id.slice(0,8)+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){notify('Export failed. Reconnect and try again.',true);}});
async function initialize(){if(configured||initializing)return;initializing=true;try{const config=await api('/api/config');mode=config.mode;$('mode').textContent=mode==='sample'?'SAMPLE MODE · NO API CALLS':'LIVE MODEL · API CHARGES APPLY';$('mode').className='badge'+(mode==='live'?' live':'');$('mode-description').textContent=mode==='sample'?'Deterministic sample rules. Requests, queue state and review decisions are real; no external model is called.':'Live OpenAI integration enabled. Submitted text is sent to the configured model; results still require review.';if(mode==='live'){$('scenario').value='normal';$('scenario').disabled=true;$('scenario-help').textContent='Failure injection is disabled in live mode.';}configured=true;$('submit-button').disabled=false;await refresh();}catch(e){notify('Cannot connect to the API. Start the FastAPI app using the README instructions. Retrying automatically.',true);}finally{initializing=false;}}
$('submit-button').disabled=true;initialize();setInterval(()=>configured?refresh():initialize(),750);
