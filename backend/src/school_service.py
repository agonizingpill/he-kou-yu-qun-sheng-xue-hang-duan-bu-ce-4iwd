import os,redis
from fastapi import HTTPException
from sqlalchemy import select,func
from .models import SchoolAnalysis,SchoolReview,Transect,Echo
from .common import get,reject,finish,transect_lock
from . import coverage_service as coverage
queue=redis.Redis.from_url(os.getenv('REDIS_URL','redis://redis:6379/0'),decode_responses=True)
def latest(db,transect_id,reviewed=False):
    t=get(db,Transect,transect_id)
    row=coverage.current_analysis(db,t)
    if row is None:return None
    if reviewed:return row if row.status=='reviewed' else None
    return row if row.status in ('done','reviewed') else None
def enqueue(db,data,user):
    c,t=transect_lock(db,data.transect_id)
    reject(db.scalar(select(func.count()).select_from(Echo).where(Echo.transect_id==t.id))<8,'至少8个回波点才可识别群落')
    reject(db.scalar(select(SchoolAnalysis).where(SchoolAnalysis.transect_id==t.id,SchoolAnalysis.data_version==t.data_version,SchoolAnalysis.status.in_(['queued','running']))) is not None,'当前版本正在识别')
    row=SchoolAnalysis(**data.model_dump(),data_version=t.data_version);db.add(row);db.flush();result=finish(db,user,'提交回波群落识别',row)
    try:queue.rpush('estuary:schools',row.id)
    except redis.RedisError:
        row.status='failed';row.error='队列暂不可用，恢复后可重新识别';db.commit();raise HTTPException(503,row.error)
    return result
def review(db,id,data,user):
    initial=get(db,SchoolAnalysis,id);c,t=transect_lock(db,initial.transect_id);row=get(db,SchoolAnalysis,id,True)
    reject(row.status not in ('done','reviewed') or row.data_version!=t.data_version,'仅最新回波版本的当前识别可复核')
    reject(coverage.current_analysis(db,t).id!=row.id,'识别已重跑，只认当前最新一次结果，请刷新后再签')
    echo_count=db.scalar(select(func.count()).select_from(Echo).where(Echo.transect_id==t.id))
    noise=row.metrics.get('noise_count',0);points=row.metrics.get('point_count',0)
    reject(points!=echo_count,'识别点数与当前回波数不一致，请重跑识别')
    if data.verdict=='no_fish':
        reject(row.metrics.get('clusters',0)!=0 or noise!=echo_count,'实测无鱼群仅在当前识别全部为噪点时才能签出')
    reject(noise>0 and len(data.noise_note.strip())<8,'存在噪点，请补充至少8字现场解释')
    cov=coverage.compute(db,t)
    signed=coverage.basis(cov,row.id,row.metrics.get('input_sha256'))
    signed.update({'verdict':data.verdict,'signed_by':user.username,'rule':coverage.RULE,'rule_version':coverage.RULE_VERSION})
    signed['signature']=coverage.signature(signed)
    row.status='reviewed';t.status='reviewed'
    review=db.scalar(select(SchoolReview).where(SchoolReview.analysis_id==row.id))
    if review is None:
        review=SchoolReview(analysis_id=row.id,**data.model_dump(),coverage=signed);db.add(review)
    else:
        for k,v in data.model_dump().items():setattr(review,k,v)
        review.coverage=signed
    db.flush();return finish(db,user,'群落与噪点复核',review)
