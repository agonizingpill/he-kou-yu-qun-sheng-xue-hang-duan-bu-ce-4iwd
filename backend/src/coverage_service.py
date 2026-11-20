"""沿程覆盖与复核证据。

口径：
- 每个回波沿程前后各 25 米计入已测；
- 已登记的缺测区间即使有回波穿过也不计为已测，只能由“已确认接续且签出实测无鱼群”的补航填上；
- 覆盖从 0 到航段终点连续不断，才算覆盖到两端；
- 只有“实测无鱼群 + 覆盖完整”的航段才免交生物样。
结论是否有效只认当前回波版本、当前最新一次识别和当前覆盖，历史签字不会继续放行。
"""
import hashlib
import json
from sqlalchemy import select
from .models import Echo,MissingInterval,Resurvey,Transect,SchoolAnalysis,SchoolReview

ECHO_HALF_WIDTH_M=25.0
TOL=1e-6
CONTIG_TOL=0.01
RULE_VERSION=1
RULE='回波沿程±25米为已测；登记缺口仅由已接续并签出实测无鱼群的补航填充；覆盖到两端且中间不断才免交生物样'
BASIS_KEYS=('transect_id','length_m','data_version','echo_count','echo_half_width_m','echo_coverage','covered','holes','complete','gaps')

def _merge(intervals,length):
    spans=sorted((max(0.0,float(a)),min(float(length),float(b))) for a,b in intervals if float(b)-float(a)>TOL)
    out=[]
    for a,b in spans:
        if out and a<=out[-1][1]+CONTIG_TOL:out[-1][1]=max(out[-1][1],b)
        else:out.append([a,b])
    return out

def _cut(intervals,cuts):
    pieces=[[a,b] for a,b in intervals]
    for c,d in cuts:
        remaining=[]
        for a,b in pieces:
            if d<=a+TOL or c>=b-TOL:remaining.append([a,b]);continue
            if c>a+TOL:remaining.append([a,min(c,b)])
            if d<b-TOL:remaining.append([max(d,a),b])
        pieces=remaining
    return pieces

def _holes(covered,length):
    out=[];cursor=0.0
    for a,b in covered:
        if a>cursor+CONTIG_TOL:out.append([round(cursor,3),round(a,3)])
        cursor=max(cursor,b)
    if cursor<length-CONTIG_TOL:out.append([round(cursor,3),round(float(length),3)])
    return out

def current_analysis(db,t):
    return db.scalar(select(SchoolAnalysis).where(SchoolAnalysis.transect_id==t.id,SchoolAnalysis.data_version==t.data_version).order_by(SchoolAnalysis.id.desc()))

def basis(cov,analysis_id,input_sha256):
    out={k:cov[k] for k in BASIS_KEYS}
    out['gaps']=[{'gap_id':g['gap_id'],'start_m':g['start_m'],'end_m':g['end_m'],'measured':g['measured'],'continuation_analysis_id':g.get('continuation_analysis_id')} for g in cov['gaps']]
    out.update({'analysis_id':analysis_id,'input_sha256':input_sha256})
    return out

def signed_payload(basis_value,verdict,signed_by):
    payload=dict(basis_value);payload.update({'verdict':verdict,'signed_by':signed_by,'rule':RULE,'rule_version':RULE_VERSION})
    return payload

def signature(payload):
    canonical=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

def _review_row(db,t):
    a=current_analysis(db,t)
    if a is None:return None,None
    return a,db.scalar(select(SchoolReview).where(SchoolReview.analysis_id==a.id))

def review_state(db,t,cov=None):
    a,r=_review_row(db,t)
    if a is None:return {'analysis_id':None,'analysis_status':None,'review_id':None,'verdict':None,'valid':False,'stale':False,'signed':None,'reason':'当前回波版本尚无识别','clusters':None,'noise_count':None,'point_count':None}
    verdict=(getattr(r,'verdict',None) or 'schools') if r else None
    base={'analysis_id':a.id,'analysis_status':a.status,'review_id':r.id if r else None,'verdict':verdict,'clusters':a.metrics.get('clusters'),'noise_count':a.metrics.get('noise_count'),'point_count':a.metrics.get('point_count')}
    if a.status!='reviewed' or r is None:
        return {**base,'valid':False,'stale':False,'signed':False,'reason':'最新一次识别尚未复核'}
    signed=getattr(r,'coverage',None) or None
    if not signed or not signed.get('signature'):
        return {**base,'valid':False,'stale':True,'signed':True,'reason':'签字缺少覆盖依据，请按当前覆盖重签'}
    stored=dict(signed);stated=stored.pop('signature')
    if signature(stored)!=stated:
        return {**base,'valid':False,'stale':True,'signed':True,'reason':'签字覆盖依据校验和不符，请重签'}
    if cov is None:cov=_compute(db,t,{},set())
    if cov is None:return {**base,'valid':False,'stale':True,'signed':True,'reason':'接续关系成环，无法重算覆盖'}
    current=basis(cov,a.id,a.metrics.get('input_sha256'))
    if any(stored.get(k)!=current.get(k) for k in BASIS_KEYS) or stored.get('analysis_id')!=a.id or stored.get('input_sha256')!=current.get('input_sha256'):
        return {**base,'valid':False,'stale':True,'signed':True,'reason':'覆盖依据在签字后发生变化，请按当前回波与缺口重算后再签'}
    return {**base,'valid':True,'stale':False,'signed':True,'signature':stated,'reason':'最新识别已复核，覆盖依据与签字一致'}

def _continuation_ok(db,res,parent_gap,memo,stack):
    child=db.get(Transect,res.continuation_id)
    if child is None or child.status=='cancelled':return False,'接续航段已撤销',None,None
    if abs(float(child.length_m)-(float(parent_gap.end_m)-float(parent_gap.start_m)))>1.0:
        return False,'补航长度与原缺测区间不一致，需重新规划',None,None
    cov=_compute(db,child,memo,stack)
    if cov is None:return False,'接续关系成环，无法复核',None,None
    state=review_state(db,child,cov)
    if not state['valid']:return False,'补航最新结论失效：'+state['reason'],None,None
    if not cov['complete']:return False,'补航回波未覆盖到两端或中间仍有断点',None,None
    if state['verdict']!='no_fish':return True,'补航已实测覆盖缺口，但未签出实测无鱼群，缺口不计为无鱼群已测',state['analysis_id'],state.get('signature')
    return True,'补航覆盖完整并已签出实测无鱼群，缺口计为无鱼群已测',state['analysis_id'],state.get('signature')

def _compute(db,t,memo,stack):
    if t.id in memo:return memo[t.id]
    if t.id in stack:return None
    stack.add(t.id)
    try:
        length=float(t.length_m)
        echoes=list(db.scalars(select(Echo).where(Echo.transect_id==t.id).order_by(Echo.id)))
        echo_cov=_merge([[e.along_m-ECHO_HALF_WIDTH_M,e.along_m+ECHO_HALF_WIDTH_M] for e in echoes],length)
        gaps=list(db.scalars(select(MissingInterval).where(MissingInterval.transect_id==t.id).order_by(MissingInterval.id)))
        covered=_cut(echo_cov,[[g.start_m,g.end_m] for g in gaps])
        gap_rows=[]
        for g in gaps:
            res=db.scalar(select(Resurvey).where(Resurvey.gap_id==g.id).order_by(Resurvey.id.desc()))
            verified=False;filled=False;status='pending';reason='缺测尚未安排补航';child_analysis=None;child_signature=None
            if res is not None and res.status=='completed':
                verified,reason,child_analysis,child_signature=_continuation_ok(db,res,g,memo,stack)
                if not verified:status='invalid'
                else:
                    child=db.get(Transect,res.continuation_id)
                    state=review_state(db,child,_compute(db,child,memo,stack))
                    filled=state['verdict']=='no_fish'
                    status='measured_no_fish' if filled else 'measured_fish'
            elif res is not None and res.status=='planned':status='planned';reason='补航在途，尚未确认接续'
            elif res is not None and res.status=='cancelled':status='cancelled';reason='补航已撤销，需重新安排'
            elif g.status=='covered':status='invalid';reason='缺测标记已接续，但补航记录缺失，需重新核验'
            if filled:covered=_merge(covered+[[g.start_m,g.end_m]],length)
            gap_rows.append({'gap_id':g.id,'start_m':round(g.start_m,3),'end_m':round(g.end_m,3),'stored_status':g.status,'status':status,'verified':verified,'measured':verified,'filled':filled,'resurvey_id':res.id if res else None,'resurvey_status':res.status if res else None,'reason':reason,'continuation_analysis_id':child_analysis,'continuation_signature':child_signature})
        covered=_merge(covered,length);hole_rows=_holes(covered,length)
        memo[t.id]={'transect_id':t.id,'length_m':round(length,3),'data_version':t.data_version,'echo_count':len(echoes),'echo_half_width_m':ECHO_HALF_WIDTH_M,'echo_coverage':[[round(a,3),round(b,3)] for a,b in echo_cov],'covered':[[round(a,3),round(b,3)] for a,b in covered],'holes':hole_rows,'complete':not hole_rows,'gaps':gap_rows,
                    'pending_gaps':[g['gap_id'] for g in gap_rows if g['status'] in ('pending','planned','cancelled','invalid')],
                    'filled_gaps':[g['gap_id'] for g in gap_rows if g['filled']]}
        return memo[t.id]
    finally:stack.discard(t.id)

def compute(db,t):
    return _compute(db,t,{},set())

def sample_requirement(cov):
    state=cov['review']
    if not state['valid']:return {'waived':False,'own_required':True,'sources':[],'reason':'当前结论未复核或已失效，不能确认免交生物样'}
    if state['verdict']!='no_fish':return {'waived':False,'own_required':True,'sources':[],'reason':'当前识别有鱼群，须交本航段最新群落的生物样'}
    if cov['complete']:return {'waived':True,'own_required':False,'sources':[],'reason':'实测无鱼群且覆盖到两端、中间不断，免交生物样'}
    inside=lambda h,g:h[0]>=float(g['start_m'])-1 and h[1]<=float(g['end_m'])+1
    outside=[h for h in cov['holes'] if not any(inside(h,g) and g['verified'] for g in cov['gaps'])]
    if outside:return {'waived':False,'own_required':True,'sources':[],'reason':'实测无鱼群但仍有缺口未由已接续补航实测：'+'、'.join(f'{a}–{b}m' for a,b in outside)}
    # 未填上的缺口已由有效接续的补航实测：有鱼群的补航本身须交样本，可作为该区间样本来源。
    return {'waived':False,'own_required':False,'sources':[g['resurvey_id'] for g in cov['gaps'] if g['verified']],'reason':'未填缺口已由已接续补航实测，样本按补航最新群落提交，原航段不另交生物样'}

def summary(db,t,memo=None,stack=None):
    memo={} if memo is None else memo;stack=set() if stack is None else stack
    cov=_compute(db,t,memo,stack)
    if cov is None:
        cov={'transect_id':t.id,'length_m':round(float(t.length_m),3),'data_version':t.data_version,'echo_count':0,'echo_half_width_m':ECHO_HALF_WIDTH_M,'echo_coverage':[],'covered':[],'holes':[[0.0,round(float(t.length_m),3)]],'complete':False,'gaps':[],'cycle':True}
    state=review_state(db,t,cov)
    if cov.get('cycle'):state={**state,'valid':False,'stale':True,'reason':'接续关系成环，无法重算覆盖'}
    sample=sample_requirement({**cov,'review':state})
    measured=round(sum(b-a for a,b in cov['covered']),3)
    return {**cov,'code':t.code,'parent_id':t.parent_id,'measured_m':measured,'measured_ratio':round(measured/cov['length_m'],4) if cov['length_m'] else 0,'review':state,'sample':sample,'waives_sample':sample['waived'],'needs_sample':sample['own_required'],'rule_version':RULE_VERSION,'rule':RULE}

def cruise(db,cruise_id=None):
    stmt=select(Transect).where(Transect.status!='cancelled').order_by(Transect.id)
    if cruise_id is not None:stmt=stmt.where(Transect.cruise_id==cruise_id)
    ts=list(db.scalars(stmt));memo={};stack=set()
    return [summary(db,t,memo,stack) for t in ts]
