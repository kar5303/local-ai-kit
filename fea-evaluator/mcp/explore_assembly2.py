# -*- coding: utf-8 -*-
"""探索第 2 輪：零件改名、網格、接合面搜尋、匯出網格。只在 M001 測試模型上動作。結果存 mcp/explore2/r2_*。"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from pmx_mcp import PrePoMax
import step_names
OUT = os.path.join(HERE, 'explore2'); os.makedirs(OUT, exist_ok=True)
STEP = os.path.join(ROOT, 'validation', 'M001', 'M001.step')
LOG = []
def log(s): print(s); LOG.append(str(s))
def jl(t):
    try: return json.loads(t)
    except Exception: return {'raw': t}
def save(name, text): open(os.path.join(OUT, 'r2_' + name), 'w', encoding='utf-8').write(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1))
def run(p, src, timeout=300):
    t, _ = p.call('run_python', source=src, timeout=timeout); d = jl(t)
    if d.get('failed'): log(f'  失敗：{d.get("error")}')
    return (d.get('output') or '').strip(), d

def main():
    p = PrePoMax()
    st = jl(p.call('get_status')[0]); m = st.get('model', {})
    md = jl(p.call('read_model')[0])
    mats = [x.get('name') if isinstance(x, dict) else str(x) for x in md.get('materials', [])]
    log(f"狀態 idle={st.get('isIdle')} 未存檔={m.get('hasUnsavedChanges')} 材料={mats}")
    if m.get('hasUnsavedChanges') and not set(mats) <= {'S420'}:
        log('目前模型不是測試模型，停止'); return
    run(p, f"pmx.new_model('ThreeD', 'MM_TON_S_C', discard_changes={bool(m.get('hasUnsavedChanges'))})")
    run(p, f"pmx.import_file(r'{STEP}')")
    topo = jl(p.call('read_topology', limit=5)[0])
    pm = [(g['name'], g['bounding_box']) for g in topo.get('geometry', [])]
    mp, how = step_names.match_parts(step_names.read_step_parts(STEP), pm)
    log(f'STEP 名稱對應：{mp}（{how}）')
    if mp:
        new = step_names.unique_names(mp)
        src = '\n'.join(f"pmx.model.geometry.parts.rename('{a}', '{b}')" for a, b in new.items())
        run(p, src)
    out, _ = run(p, "print(list(pmx.model.geometry.parts.get_names()))\nprint(list(pmx.model.geometry.parts.get_element_type_names()))")
    log(f'改名後幾何零件 / 元素型別名稱：{out}')
    out, _ = run(p, "names = list(pmx.model.geometry.parts.get_names())\nfor n in names: print(n, pmx.model.geometry.parts.get_element_types(n))")
    log(f'網格前元素型別：{out}')
    out, _ = run(p, "names = list(pmx.model.geometry.parts.get_names())\nr = pmx.model.mesh.generate(names)\nprint(r)", timeout=600)
    log(f'網格結果：{out}')
    out, _ = run(p, "names = list(pmx.model.parts.get_names())\nprint(names)\nfor n in names: print(n, pmx.model.parts.get_element_types(n))\nprint('nodes', pmx.model.nodes.count)")
    log(f'網格零件：{out}')
    t, _ = p.call('read_topology', limit=40); save('topo_mesh.json', t)
    mesh_inp = os.path.join(OUT, 'r2_mesh.inp')
    out, _ = run(p, f"names = list(pmx.model.parts.get_names())\npmx.export_mesh(names, r'{mesh_inp}')\nprint('ok')")
    log(f'匯出網格：{out} 存在={os.path.exists(mesh_inp)}')
    out, _ = run(p, "r = pmx.model.contact_pairs.search(1.0, pair_type='Tie', group_by='None')\nprint(type(r).__name__)\nprint(repr(r)[:3000])\ntry:\n    print([a for a in dir(r) if not a.startswith('_')])\n    for x in list(r)[:20]:\n        print(type(x).__name__, repr(x)[:500], [a for a in dir(x) if not a.startswith('_')])\nexcept Exception as e:\n    print('ERR', e)", timeout=300)
    save('contact_search.txt', out); log('接合面搜尋結果已存')
    out, _ = run(p, "d = pmx.model.nodes.get_data()\nprint(type(d).__name__, [a for a in dir(d) if not a.startswith('_')][:40])\nprint(repr(d)[:800])")
    save('nodes_data.txt', out)
    out, _ = run(p, f"print(pmx.view.save_image(r'{os.path.join(OUT, 'r2_view.png')}'))")
    log(f'截圖：{out}')
    log('完成')

if __name__ == '__main__':
    try: main()
    finally: open(os.path.join(OUT, 'r2_log.txt'), 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
