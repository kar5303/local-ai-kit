# -*- coding: utf-8 -*-
"""探索組件流程用的 API（匯入 STEP → 網格 → 讀面資料），結果存 mcp/explore2/。只會在測試模型上動作。"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from pmx_mcp import PrePoMax
OUT = os.path.join(HERE, 'explore2'); os.makedirs(OUT, exist_ok=True)
STEP = os.path.join(ROOT, 'validation', 'M001', 'M001.step')
LOG = []
def log(s): print(s); LOG.append(str(s))
def jl(t):
    try: return json.loads(t)
    except Exception: return {'raw': t}
def save(name, text): open(os.path.join(OUT, name), 'w', encoding='utf-8').write(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1))
def run(p, src, timeout=300):
    t, _ = p.call('run_python', source=src, timeout=timeout); d = jl(t)
    if d.get('failed'): log(f'  失敗：{d.get("error")} {d.get("messages")}')
    return (d.get('output') or '').strip(), d

def main():
    p = PrePoMax()
    st = jl(p.call('get_status')[0]); m = st.get('model', {})
    log(f"狀態 idle={st.get('isIdle')} 未存檔={m.get('hasUnsavedChanges')}")
    discard = False
    if m.get('hasUnsavedChanges'):
        md = jl(p.call('read_model')[0])
        mats = [x.get('name') if isinstance(x, dict) else str(x) for x in md.get('materials', [])]
        if set(mats) <= {'S420'}: discard = True
        else: log('目前模型有未存檔的修改且不是測試模型，停止'); return
    for path in ['pmx.model.geometry.parts', 'GeometryParts', 'pmx.model.mesh.setup_items', 'MeshSetupItems',
                 'pmx.model.constraints', 'Constraints', 'pmx.model.contact_pairs', 'ContactPairs',
                 'pmx.model.surface_interactions', 'pmx.model.parts', 'pmx.model.node_sets', 'pmx.model.surfaces',
                 'pmx.model.nodes', 'pmx.model.elements', 'pmx.view', 'Result', 'Result.mesh', 'pmx.selection', 'Step', 'Steps']:
        t, _ = p.call('describe_api', path=path); save('api_' + path.replace('.', '_') + '.txt', t)
    log('API 說明已存')
    run(p, f"pmx.new_model('ThreeD', 'MM_TON_S_C', discard_changes={discard})")
    out, d = run(p, f"pmx.import_file(r'{STEP}')\nprint(pmx.model.geometry.parts.get_names())\nprint(pmx.model.parts.get_names())")
    log(f'匯入後 幾何零件 / 網格零件：{out}')
    t, _ = p.call('read_topology', limit=60); save('topo_geometry.json', t)
    save('model_after_import.json', p.call('read_model')[0])
    names, _ = run(p, "import json\nprint(json.dumps(pmx.model.geometry.parts.get_names()))")
    try: gnames = json.loads(names)
    except Exception: gnames = []
    out, d = run(p, f"r = pmx.model.mesh.generate({gnames!r})\nprint(r)", timeout=600)
    log(f'網格結果：{out}')
    out, d = run(p, "import json\nprint(json.dumps(pmx.model.parts.get_names()))\nfor n in pmx.model.parts.get_names():\n    pt = pmx.model.parts.get(n)\n    print(n, type(pt).__name__, [a for a in dir(pt) if not a.startswith('_')])")
    log(f'網格零件：{out}')
    t, _ = p.call('read_topology', limit=48); save('topo_mesh.json', t)
    save('model_after_mesh.json', p.call('read_model')[0])
    out, d = run(p, "n = pmx.model.parts.get_names()[0]\npt = pmx.model.parts.get(n)\nfor a in ['get_face_data','get_edge_data','get_vertex_data']:\n    f = getattr(pt, a, None)\n    print(a, f.__doc__ if f else None)")
    log(f'零件方法：{out}')
    out, d = run(p, "n = pmx.model.parts.get_names()[0]\npt = pmx.model.parts.get(n)\ntry:\n    r = pt.get_face_data()\n    print(str(r)[:3000])\nexcept Exception as e:\n    print('ERR', e)")
    save('face_data_sample.txt', out)
    log('完成')

if __name__ == '__main__':
    try: main()
    finally: open(os.path.join(OUT, 'log.txt'), 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
