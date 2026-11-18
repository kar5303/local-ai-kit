# -*- coding: utf-8 -*-
"""
V-007：兩段不同斷面的方管以 Tie 綁定成懸臂，自由端 1000 N（經 PrePoMax MCP 全自動）
驗證：多零件的應變能佔比（剛性貢獻）、質量佔比、排名，與梁理論解析解、Claude 端 CalculiX 參考解比對。
Part A：x -400~-200，30x30 / 20x20（固定端）；Part B：x -200~0，30x30 / 24x24（受力端）
"""
import os, sys, json, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from pmx_mcp import PrePoMax
import fea_eval

F = 1000.0; E, NU, RHO = 210000.0, 0.28, 7.8e-9
MESH = os.path.join(ROOT, 'validation', 'V007', 'V007_mesh.inp')
OUTDIR = os.path.join(ROOT, 'validation', 'V007'); JOB = 'V007'
TEST_MATERIALS = (['S420'], ['Sec-A', 'Sec-B'])
LOG = []
def log(s): print(s); LOG.append(str(s))
def jl(t):
    try: return json.loads(t)
    except Exception: return {'raw': t}
def run(p, src, timeout=120):
    t, _ = p.call('run_python', source=src, timeout=timeout); d = jl(t)
    if d.get('failed'):
        log('---- Python 錯誤 ----'); log(d.get('error')); log(d.get('messages')); raise SystemExit(1)
    if d.get('messages'): log(f"  PrePoMax 訊息：{d['messages']}")
    return (d.get('output') or '').strip(), d

def faces_where(topo, cond):
    out = []
    for part in topo.get('femodel', []):
        for fc in part.get('faces', []):
            c, n = fc.get('centroid'), fc.get('normal')
            if c and n and cond(c, n):
                out.append({'part': part['name'], 'part_id': part['id'], 'id': fc['id'], 'centroid': c, 'normal': n, 'area': fc.get('area')})
    return out

def main():
    p = PrePoMax()
    st = jl(p.call('get_status')[0]); m = st.get('model', {})
    log(f"1. 狀態：idle={st.get('isIdle')} 未存檔={m.get('hasUnsavedChanges')}")
    if not st.get('isIdle') or st.get('isReadOnly'): log('PrePoMax 忙碌或唯讀，停止'); return
    discard = False
    if m.get('hasUnsavedChanges'):
        md = jl(p.call('read_model')[0])
        names = lambda k: sorted((x.get('name') if isinstance(x, dict) else str(x)) for x in md.get(k, []))
        if names('materials') == ['S420'] and names('sections') in (['Sec-1'], ['Sec-A', 'Sec-B']):
            log('   目前模型是之前驗證留下的測試模型，以新模型取代'); discard = True
        else:
            log('目前模型有未存檔的修改，為了不丟失你的工作，停止。'); return

    log('2. 新模型＋匯入兩零件網格')
    run(p, f"pmx.new_model('ThreeD', 'MM_TON_S_C', discard_changes={discard})")
    run(p, f"pmx.import_file(r'{MESH}')", timeout=300)
    topo_t, _ = p.call('read_topology', limit=240)
    open(os.path.join(OUTDIR, 'V007_topology.json'), 'w', encoding='utf-8').write(topo_t)
    topo = jl(topo_t)
    parts = [(pt['name'], pt['id'], pt.get('bounding_box')) for pt in topo.get('femodel', [])]
    log(f'   零件：{parts}')
    if len(parts) != 2: log('   零件數不是 2，停止'); return
    # 依包圍盒判斷 A（含 x=-400）與 B
    pa = [x for x in parts if min(x[2][0], x[2][3]) < -399][0]; pb = [x for x in parts if x is not pa][0]
    log(f'   Part A = {pa[0]}，Part B = {pb[0]}')

    log('3. 材料與截面')
    run(p, f"pmx.model.materials.add('S420', [pmx.create.elastic({E}, {NU}), pmx.create.density({RHO})])")
    run(p, f"pmx.model.sections.add_solid('Sec-A', 'S420', pmx.create.region('PartName', '{pa[0]}'))")
    run(p, f"pmx.model.sections.add_solid('Sec-B', 'S420', pmx.create.region('PartName', '{pb[0]}'))")

    log('4. Tie 綁定（介面 x = -200）')
    fa = faces_where(topo, lambda c, n: abs(c[0] + 200) < 1e-3 and n[0] > 0.99)
    fb = faces_where(topo, lambda c, n: abs(c[0] + 200) < 1e-3 and n[0] < -0.99)
    log(f'   A 側面（法向 +X）：{fa}'); log(f'   B 側面（法向 -X）：{fb}')
    if len(fa) != 1 or len(fb) != 1: log('   介面面數不是各 1，停止'); return
    desc_t, _ = p.call('describe_api', path='pmx.model.constraints')
    open(os.path.join(OUTDIR, 'api_constraints.txt'), 'w', encoding='utf-8').write(desc_t)
    desc = jl(desc_t)
    tie = [mm for mm in desc.get('members', []) if mm['name'] in ('add_tie',)]
    if not tie:
        log('   找不到 add_tie，API 說明已存 api_constraints.txt，停止'); return
    params = [pp['name'] for pp in tie[0].get('parameters', [])]
    log(f"   add_tie 參數：{params}")
    run(p, f"sel_master = pmx.create.selection_by_ids('femodel', 'surfaces', 'surfaces', {{{fa[0]['part_id']}: [{fa[0]['id']}]}})\nprint(sel_master.preview())")
    out, _ = run(p, f"sel_slave = pmx.create.selection_by_ids('femodel', 'surfaces', 'surfaces', {{{fb[0]['part_id']}: [{fb[0]['id']}]}})\nprint(sel_slave.preview())")
    log(f'   slave 預覽：{out}')
    mk = [x for x in params if any(w in x.lower() for w in ('master', 'main', 'primary'))]
    sk = [x for x in params if any(w in x.lower() for w in ('slave', 'secondary'))]
    if not mk or not sk:
        log('   無法辨識 master/slave 參數名稱，停止（請把 api_constraints.txt 給 Claude）'); return
    # 較大的 A 面為 master，較小的 B 面為 slave
    run(p, f"pmx.model.constraints.add_tie('Tie-1', {mk[0]}=sel_master, {sk[0]}=sel_slave)")

    log('5. 靜態步、固定端、端面力')
    run(p, "step = pmx.model.steps.add_static('Step-1')")
    out, _ = run(p, "fix_sel = pmx.create.selection_by_box('femodel', 'nodes', [-400.5, 0, 0], [-399.5, 50, 50], 'Node')\nprint(fix_sel.preview())")
    log(f'   固定端預覽：{out}')
    run(p, "step.bcs.add_fixed('Fix-1', fix_sel)")
    ft = faces_where(topo, lambda c, n: abs(c[0]) < 1e-3 and n[0] > 0.99)
    log(f'   受力面：{ft}')
    if len(ft) != 1: log('   受力面數不是 1，停止'); return
    out, _ = run(p, f"load_sel = pmx.create.selection_by_ids('femodel', 'surfaces', 'surfaces', {{{ft[0]['part_id']}: [{ft[0]['id']}]}})\nprint(load_sel.preview())")
    log(f'   受力面預覽：{out}')
    run(p, f"step.loads.add_surface_traction('Tip-1', load_sel, f2={-F})")

    log('6. 求解')
    run(p, f"pmx.jobs.add('{JOB}')"); p.call('run_job', name=JOB)
    status = None
    for _ in range(300):
        d = jl(p.call('get_job', name=JOB)[0]); status = (d.get('value') or {}).get('status')
        if status in ('OK', 'Killed', 'TimedOut', 'Failed', 'FailedWithResults'): break
        time.sleep(2)
    log(f'   求解狀態：{status}')
    if status != 'OK':
        d = jl(p.call('get_job', name=JOB, include_output=True)[0]); log(str(d.get('value') or d)[:3000]); return
    wd, _ = run(p, "print(pmx.work_directory)")
    inp = os.path.join(wd, JOB + '.inp'); frd = os.path.join(wd, JOB + '.frd')

    log('7. 評估')
    model = fea_eval.read_inp(inp)
    names = {pa[0]: 'Part A（固定端，20x20 內孔）', pb[0]: 'Part B（受力端，24x24 內孔）'}
    cfg = {'parts': {s: {'display_name': names.get(s, s), 'material_class': 'ductile', 'allowable': 420, 'moving_axis': None}
                     for s in model['part_sets']},
           'safety_factor_required': 1.0, 'weights': {'strength': 0.5, 'stiffness': 0.5},
           'exclude_bc_radius': 20, 'hotspot_top_percent': 5, 'hotspot_cluster_distance': 40, 'regions': []}
    log(f"   .inp 零件集合：{model['part_sets']}")
    json.dump(cfg, open(os.path.join(OUTDIR, 'fea_config.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    r = fea_eval.evaluate(inp, frd, cfg)
    fea_eval.write_outputs(r, os.path.join(OUTDIR, 'V007_評估結果'), cfg)
    for k in r['ranking']:
        v = r['parts'][k]
        log(f"   {v['display_name']}：應變能 {v['strain_energy']:.4f} N·mm，剛性貢獻 {v['SE_share']:.2f}%，質量 {v['mass']*1000:.5f} kg（{v['mass_share']:.2f}%），效率比 {v['eff']:.3f}，UR {v['UR']}，風險佔比 {v['risk_share']:.2f}%")
    res = fea_eval.read_frd(frd)
    tip = [res['DISP'][n][1] for n, c in r['model']['nodes'].items() if abs(c[0]) < 1e-6 and n in res['DISP']]
    log(f"   總應變能 {r['total_se']:.4f}；外力功 {r['work_check']}；端面平均 U2 {sum(tip)/len(tip):.6f} mm")
    log('8. 對照值')
    log('   梁理論（彎曲+剪切腹板近似）：A 83.23% / B 16.77%，端面位移 1.981337 mm')
    log('   梁理論（只算彎曲）：A 83.74% / B 16.26%，端面位移 1.959665 mm')
    log('   Claude 端 CalculiX 2.21 參考解（*TIE，一致節點力）：A 83.01% / B 16.99%，總應變能 989.142，端面位移 1.981591 mm')
    log('   質量：A 0.78 kg / B 0.50544 kg（A 60.68%）')
    log('完成。修改可在 編輯 → 編輯歷程 檢視或移除。')

if __name__ == '__main__':
    try: main()
    finally: open(os.path.join(OUTDIR, 'V007_log.txt'), 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
