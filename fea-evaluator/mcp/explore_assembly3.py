# -*- coding: utf-8 -*-
"""探索第 3 輪：網格產生的完整回傳（M001 測試模型，延續第 2 輪的模型）"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from pmx_mcp import PrePoMax
OUT = os.path.join(HERE, 'explore2')
LOG = []
def log(s): print(s); LOG.append(str(s))
def raw(p, src, timeout=600):
    t, _ = p.call('run_python', source=src, timeout=timeout); log('>>> ' + src.splitlines()[0]); log(t[:6000]); return t
def main():
    p = PrePoMax()
    raw(p, "print(list(pmx.model.geometry.parts.get_names()))\nprint(list(pmx.model.mesh.setup_items.get_names()))")
    raw(p, "pmx.model.mesh.help()")
    raw(p, "g = pmx.model.geometry.parts.get('SPINDLE_S45C')\nprint(type(g).__name__, [a for a in dir(g) if not a.startswith('_')])")
    raw(p, "names = list(pmx.model.geometry.parts.get_names())\nr = pmx.model.mesh.generate(names)\nprint('RESULT', repr(r))", timeout=600)
    raw(p, "print(list(pmx.model.parts.get_names()), pmx.model.nodes.count)\nfor n in pmx.model.parts.get_names(): print(n, pmx.model.parts.get_element_types(n))")
    t, _ = p.call('read_topology', limit=40); open(os.path.join(OUT, 'r3_topo_mesh.json'), 'w', encoding='utf-8').write(t)
    mesh_inp = os.path.join(OUT, 'r3_mesh.inp')
    raw(p, f"pmx.export_mesh(list(pmx.model.parts.get_names()), r'{mesh_inp}')\nprint('ok')")
    raw(p, "r = pmx.model.contact_pairs.search(1.0, pair_type='Tie', group_by='None')\nprint(type(r).__name__, repr(r)[:2000])\nprint([a for a in dir(r) if not a.startswith('_')])\nfor x in list(r)[:12]: print(type(x).__name__, repr(x)[:600], [a for a in dir(x) if not a.startswith('_')])")
    raw(p, "g = pmx.model.geometry.parts.get('BASE_FC300')\nprint(g.volume, g.center_of_mass, g.surface_area)\nprint(repr(g.get_face_data())[:1500])")
    log('完成')
if __name__ == '__main__':
    try: main()
    finally: open(os.path.join(OUT, 'r3_log.txt'), 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
