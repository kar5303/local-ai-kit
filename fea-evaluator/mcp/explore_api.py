# -*- coding: utf-8 -*-
"""唯讀探索：讀取 PrePoMax 狀態、目前模型、Python API 結構。不修改任何模型。結果存到 mcp\\explore\\"""
import os, json
from pmx_mcp import PrePoMax, HERE

OUT = os.path.join(HERE, 'explore')
os.makedirs(OUT, exist_ok=True)

def save(name, text):
    with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
        f.write(text)
    print(f'  已存：explore\\{name}（{len(text)} 字元）')

p = PrePoMax()
print('1. 狀態'); save('status.json', p.call('get_status')[0])
print('2. 目前模型'); save('model.json', p.call('read_model')[0])
paths = ['pmx', 'pmx.model', 'pmx.create', 'pmx.geometry', 'pmx.model.geometry', 'pmx.model.parts',
         'pmx.model.materials', 'pmx.model.sections', 'pmx.model.steps', 'Model.Steps',
         'Loads', 'BoundaryConditions', 'pmx.model.mesh', 'pmx.jobs', 'pmx.results',
         'Result', 'Result.mesh', 'Result.steps', 'pmx.import_file', 'pmx.open', 'pmx.new_model']
print('3. API 結構')
for path in paths:
    try:
        t, _ = p.call('describe_api', path=path)
    except Exception as e:
        t = f'ERROR: {e}'
    save('api_' + path.replace('.', '_') + '.txt', t)
print('完成（未修改模型）')
