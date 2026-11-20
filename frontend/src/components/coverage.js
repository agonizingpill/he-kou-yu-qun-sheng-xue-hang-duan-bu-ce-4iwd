import {store} from '../stores/workspace.js';
import {esc} from '../utils/format.js';
import {badge} from './controls.js';

export const coverageFor=id=>store.data.coverage?.find(c=>c.transect_id===id);
export const verdictLabel=v=>({no_fish:'实测无鱼群',schools:'有鱼群'}[v]||'—');

export function coverageTrack(cov){
  if(!cov||!Array.isArray(cov.covered))return '<p class="hint">缺少覆盖计算。</p>';
  const len=cov.length_m||1,pos=v=>Math.max(0,Math.min(100,v/len*100));
  const spans=(cov.covered||[]).map(([a,b])=>`<i class="span-covered" style="left:${pos(a)}%;width:${pos(b)-pos(a)}%"></i>`).join('');
  const holes=(cov.holes||[]).map(([a,b])=>`<i class="span-hole" style="left:${pos(a)}%;width:${pos(b)-pos(a)}%"></i>`).join('');
  const gaps=(cov.gaps||[]).map(g=>`<i class="span-gap ${g.filled?'filled':g.verified?'measured-fish':'open'}" style="left:${pos(g.start_m)}%;width:${pos(g.end_m)-pos(g.start_m)}%" title="缺测#${g.gap_id}"></i>`).join('');
  return `<div class="coverage-track wide">${spans}${holes}${gaps}</div><small class="coverage-legend"><span><i class="span-covered"></i>已测 ${cov.measured_m}m / ${Math.round(cov.length_m)}m</span><span><i class="span-hole"></i>未测齐 ${(cov.holes||[]).length} 段</span>${(cov.gaps||[]).length?'<span><i class="span-gap open"></i>登记缺口</span>':''}</small>`;
}

export function coverageStatus(cov){
  if(!cov)return badge('无覆盖记录');
  const r=cov.review||{};
  if(!r.analysis_id)return badge('尚未识别','warning');
  if(!r.valid)return badge(r.stale?'签字已失效':'待复核','warning');
  if(cov.waives_sample)return badge('免交生物样','good');
  if(!cov.sample?.own_required)return badge('缺口已由补航实测','good');
  if(!cov.complete)return badge('未测齐，需生物样','warning');
  return badge('覆盖完整，需生物样');
}

export function sampleNote(cov){
  if(!cov)return '—';
  if(cov.review&&cov.review.analysis_id&&!cov.review.valid)return esc(cov.review.reason);
  return esc(cov.sample?.reason||'—');
}

export function gapLine(g){
  const state=g.filled?badge('已由无鱼群补航填上','good'):g.verified?badge('已接续·有鱼群','warning'):g.status==='planned'?badge('补航在途','warning'):badge('未测齐','warning');
  return `<b>缺测#${g.gap_id} / ${g.start_m}–${g.end_m}m</b>${state}<small>${esc(g.reason||'')}</small>`;
}

export function coverageRows(ts){
  return ts.map(t=>{
    const cov=coverageFor(t.id)||{};
    return [esc(t.code)+(t.parent_id?' · 补航':''),coverageTrack(cov),coverageStatus(cov),sampleNote(cov)];
  });
}
