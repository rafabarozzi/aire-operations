from typing import Annotated
from uuid import UUID
from fastapi import Depends, FastAPI, Header, HTTPException
from . import models as m
from .auth import Identity, identity, require
from .routes import COMMANDS
from .db import connect

app = FastAPI(title='AIRE Operations',version='1.0.0')
Actor = Annotated[Identity,Depends(identity)]
Idempotency = Annotated[str,Header(alias='Idempotency-Key',min_length=1,max_length=128)]
Version = Annotated[str,Header(alias='If-Match',pattern=r'^"[0-9]+"$',description='Quoted aggregate version')]

def read(actor):
    require(actor,'reader','operator','coordinator','admin')

@app.post('/v1/incidents',response_model=m.Incident,status_code=201)
def create(body:m.Create, actor:Actor, key:Idempotency):
    from .service import write
    return write(actor,key,None,None,'create',body)

@app.get('/v1/incidents',response_model=list[m.Incident])
def incidents(actor:Actor,limit:int=100,offset:int=0):
    from .service import present
    read(actor)
    if not (1 <= limit <= 500 and offset >= 0):
        raise HTTPException(422,'Invalid page')
    with connect() as db:
        return [present(row['body']) for row in db.execute(
            'SELECT body FROM incidents WHERE tenant=%s ORDER BY id LIMIT %s OFFSET %s',(actor.tenant,limit,offset))]

@app.get('/v1/incidents/{incident_id}',response_model=m.Incident)
def incident(incident_id:UUID,actor:Actor):
    from .service import lookup,present
    read(actor)
    with connect() as db:
        return present(lookup(db,actor.tenant,incident_id))

@app.get('/v1/incidents/{incident_id}/timeline',response_model=list[m.Event])
def timeline(incident_id:UUID,actor:Actor,after_version:int=0,limit:int=100):
    from .service import lookup
    read(actor)
    if not (1 <= limit <= 500 and after_version >= 0):
        raise HTTPException(422,'Invalid page')
    with connect() as db:
        lookup(db,actor.tenant,incident_id)
        return [r['body'] for r in db.execute('SELECT body FROM events WHERE tenant=%s AND incident_id=%s AND version>%s ORDER BY version LIMIT %s',(actor.tenant,incident_id,after_version,limit))]

@app.get('/v1/publications/{audience}/{incident_id}',response_model=list[m.Publication])
def publications(audience:m.Audience,incident_id:UUID,actor:Actor):
    require(actor,'read:'+audience)
    with connect() as db:
        rows=db.execute("SELECT body FROM publications WHERE tenant=%s AND incident_id=%s AND audience=%s AND body ? 'approved_by' ORDER BY body->>'published_at',id",(actor.tenant,incident_id,audience))
        return [r['body'] for r in rows]

class PolicyView(m.Model):
    version:int
    policy:m.OrgPolicy

@app.get('/v1/policy',response_model=PolicyView)
def org_policy(actor:Actor):
    from .service import get_policy
    read(actor)
    with connect() as db:
        return get_policy(db,actor.tenant)

@app.put('/v1/policy',response_model=PolicyView)
def set_policy(body:m.OrgPolicy,actor:Actor,key:Idempotency,version:Version):
    from .service import write
    return write(actor,key,None,int(version[1:-1]),'org-policy',body)

def register_command(name,model):
    def command(incident_id:UUID,body:model,actor:Actor,key:Idempotency,version:Version):
        from .service import write
        return write(actor,key,incident_id,int(version[1:-1]),name,body)
    app.post('/v1/incidents/{incident_id}/'+name,response_model=m.Incident,
             operation_id='incident_'+name.replace('/','_').replace('-','_'))(command)

for name,model in COMMANDS.items():
    register_command(name,model)
