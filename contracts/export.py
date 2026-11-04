"""Run from repository root: python -m contracts.export"""
import json
from pathlib import Path
from operations.api import app

folder=Path(__file__).parent/'v1'
spec=app.openapi()
for name,obj in [('openapi',spec),('schemas',{'$schema':'https://json-schema.org/draft/2020-12/schema',
    '$id':'urn:aire:operations:schemas:1.0.0','$defs':json.loads(json.dumps(spec['components']['schemas']).replace('#/components/schemas/','#/$defs/'))})]:
    (folder/(name+'.json')).write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')
