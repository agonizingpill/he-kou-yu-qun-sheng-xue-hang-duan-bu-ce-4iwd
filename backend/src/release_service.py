import json,hashlib
from sqlalchemy import select
from .models import Cruise,Transect,Echo,BioSample,MissingInterval,SurveyRelease,SchoolReview,NetCalibration,Net,Resurvey
from .common import get,reject,finish,cruise_lock,dump
from .school_service import latest
from . import coverage_service as coverage

def freeze(db,data,user):
    c=cruise_lock(db,data.cruise_id);reject(c.status!='surveyed','先完成缺测接续并结束现场调查')
    ts=list(db.scalars(select(Transect).where(Transect.cruise_id==c.id,Transect.status!='cancelled').order_by(Transect.id)));reject(not ts,'航次没有有效航段')
    memo={};stack=set();layers=[]
    for t in ts:
        cov=coverage.summary(db,t,memo,stack)
        state=cov['review']
        reject(not state['valid'],'航段'+t.code+'的最新群落/噪点结论未复核或已失效：'+state['reason'])
        a=latest(db,t.id,True);reject(not a,'航段'+t.code+'的最新群落/噪点尚未复核')
        samples=list(db.scalars(select(BioSample).where(BioSample.transect_id==t.id,BioSample.analysis_id==a.id)))
        if cov['needs_sample']:reject(not samples,'航段'+t.code+'尚不能免交生物样：'+cov['sample']['reason']+'；须补测覆盖或提交最新群落对应的生物样')
        for src in cov['sample'].get('sources',[]):
            src_res=get(db,Resurvey,src);src_t=get(db,Transect,src_res.continuation_id);src_a=latest(db,src_t.id,True)
            reject(src_a is not None and db.scalar(select(BioSample).where(BioSample.transect_id==src_t.id,BioSample.analysis_id==src_a.id)) is not None,'航段'+t.code+'的缺口由补航'+src_t.code+'实测有鱼群，但该补航缺少最新群落对应的生物样')
        layers.append({'transect':dump(t),'echoes':[dump(e) for e in db.scalars(select(Echo).where(Echo.transect_id==t.id))],'analysis':dump(a),'review':dump(db.scalar(select(SchoolReview).where(SchoolReview.analysis_id==a.id))),'samples':[dump(s) for s in samples],'calibrations':[dump(get(db,NetCalibration,s.calibration_id)) for s in samples],'nets':[dump(get(db,Net,get(db,NetCalibration,s.calibration_id).net_id)) for s in samples],'coverage':cov,'coverage_basis':a.metrics.get('input_sha256') and {'analysis_id':a.id,'input_sha256':a.metrics.get('input_sha256'),'signature':state.get('signature'),'rule_version':coverage.RULE_VERSION,'echo_half_width_m':coverage.ECHO_HALF_WIDTH_M}})
    snap={'cruise':dump(c),'layers':layers,'gaps':[dump(g) for g in db.scalars(select(MissingInterval).join(Transect).where(Transect.cruise_id==c.id))],'coverage_rule':{'version':coverage.RULE_VERSION,'rule':coverage.RULE,'echo_half_width_m':coverage.ECHO_HALF_WIDTH_M}}
    # JSON列只存可复现的JSON值，时间统一ISO格式。
    snap=json.loads(json.dumps(snap,default=str,ensure_ascii=False));canonical=json.dumps(snap,sort_keys=True,ensure_ascii=False,separators=(',',':'));sha=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    row=SurveyRelease(cruise_id=c.id,recipient=data.recipient,snapshot=snap,canonical_json=canonical,sha256=sha);db.add(row);c.status='frozen';db.flush();return finish(db,user,'封存调查图层',row)
