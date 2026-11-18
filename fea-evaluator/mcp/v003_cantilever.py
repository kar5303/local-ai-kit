# -*- coding: utf-8 -*-
"""
V-003：方管懸臂，自由端集中力 → 剛性驗證（全自動，經由 PrePoMax MCP）
流程：檢查狀態 → 新模型 → 匯入網格 → 材料/截面 → 靜態步 → 固定端 → 端面力 → 求解 → 評估 → 與理論比對
會在 PrePoMax 建立新模型（目前模型有未存檔修改時會停止，不會丟棄你的工作）。
"""
import os, sys, json, time, math
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from pmx_mcp import PrePoMax
import fea_eval

F = 1000.0          # N，-Y 方向
L, B, H, b, h = 400.0, 30.0, 30.0, 20.0, 20.0
E, NU, RHO = 210000.0, 0.28, 7.8e-9
MESH = os.path.join(ROOT, 'validation', 'V003', 'V003_mesh.inp')
OUTDIR = os.path.join(ROOT, 'validation', 'V003')
JOB = 'V003'
LOG = []

def log(s):
    print(s); LOG.append(str(s))

def jl(text):
    try:
        return json.loads(text)
    except Exception:
        return {'raw': text}

def run(p, src, timeout=120):
    t, _ = p.call('run_python', source=src, timeout=timeout)
    d = jl(t)
    if d.get('failed'):
        log('---- Python 錯誤 ----'); log(d.get('error')); log(d.get('messages'))
        raise SystemExit(1)
    if d.get('messages'):
        log(f"  PrePoMax 訊息：{d['messages']}")
    return (d.get('output') or '').strip(), d

def find_end_face(topo):
    """在 read_topology 的結果中找形心 x≈0、法向≈(+1,0,0) 的面。回傳 {part_id, id, ...}。"""
    found = []
    def walk(o, part_id=None):
        if isinstance(o, dict):
            pid = o.get('partId', o.get('part_id', o.get('id') if 'faces' in o else part_id))
            for k, v in o.items():
                if k.lower() in ('faces', 'surfaces') and isinstance(v, list):
                    for fc in v:
                        if isinstance(fc, dict):
                            c = fc.get('centroid'); n = fc.get('normal')
                            if c and n and abs(c[0]) < 1e-3 and n[0] > 0.99:
                                found.append({'part_id': pid, 'id': fc.get('id', fc.get('selectionId')), 'centroid': c, 'normal': n, 'area': fc.get('area')})
                else:
                    walk(v, pid)
        elif isinstance(o, list):
            for x in o:
                walk(x, part_id)
    walk(topo)
    return found[0] if found else None

def main():
    p = PrePoMax()
    st = jl(p.call('get_status')[0])
    m = st.get('model', {})
    log(f"1. 狀態：idle={st.get('isIdle')} readOnly={st.get('isReadOnly')} 模型={m.get('name')} 未存檔={m.get('hasUnsavedChanges')}")
    if not st.get('isIdle') or st.get('isReadOnly'):
        log('PrePoMax 忙碌或唯讀，停止'); return
    discard = False
    if m.get('hasUnsavedChanges'):
        md = jl(p.call('read_model')[0])
        names = lambda key: sorted((x.get('name') if isinstance(x, dict) else str(x)) for x in md.get(key, []))
        if names('materials') == ['S420'] and names('sections') == ['Sec-1']:
            log('   目前模型是上一次 V-003 執行留下的測試模型，將以新模型取代')
            discard = True
        else:
            log('目前模型有未存檔的修改，為了不丟失你的工作，停止。請先存檔或關閉該模型後再執行。'); return

    log('2. 新模型（ThreeD, MM_TON_S_C）並匯入網格')
    run(p, f"pmx.new_model('ThreeD', 'MM_TON_S_C', discard_changes={discard})")
    run(p, f"pmx.import_file(r'{MESH}')", timeout=300)
    out, _ = run(p, "print(list(pmx.model.parts.get_names()))")
    parts = eval(out)
    log(f'   零件：{parts}')
    part = parts[0]

    log('3. 材料、截面、靜態步')
    run(p, f"pmx.model.materials.add('S420', [pmx.create.elastic({E}, {NU}), pmx.create.density({RHO})])")
    run(p, f"pmx.model.sections.add_solid('Sec-1', 'S420', pmx.create.region('PartName', '{part}'))")
    run(p, "step = pmx.model.steps.add_static('Step-1')")

    log('4. 固定端（x = -400 的節點），先預覽數量')
    out, _ = run(p, "fix_sel = pmx.create.selection_by_box('femodel', 'nodes', [-400.5, 0, 0], [-399.5, 50, 50], 'Node')\nprint(fix_sel.preview())")
    log(f'   預覽：{out}')
    run(p, "step.bcs.add_fixed('Fix-1', fix_sel)")

    log(f'5. 自由端（x = 0 端面）表面力 F2 = -{F} N：用 read_topology 找面')
    topo_t, _ = p.call('read_topology', part_names=[part], limit=240)
    with open(os.path.join(OUTDIR, 'V003_topology.json'), 'w', encoding='utf-8') as f:
        f.write(topo_t)
    topo = jl(topo_t)
    face = find_end_face(topo)
    log(f'   找到的面：{face}')
    if face:
        src = f"load_sel = pmx.create.selection_by_ids('femodel', 'surfaces', 'surfaces', {{{face['part_id']}: [{face['id']}]}})"
    else:
        log('   topology 找不到，改用點選 (0, 12.5, 25)')
        src = "load_sel = pmx.create.selection_by_pick('femodel', 'surfaces', [0.0, 12.5, 25.0], 'Surface')"
    out, _ = run(p, src + "\nprint(load_sel.preview())")
    log(f'   預覽：{out}')
    if 'count=0' in out:
        log('   選取數量為 0，停止'); return
    run(p, f"step.loads.add_surface_traction('Tip-1', load_sel, f2={-F})")

    log('6. 建立分析並求解')
    run(p, f"pmx.jobs.add('{JOB}')")
    t, _ = p.call('run_job', name=JOB)
    log(f'   run_job：{t[:300]}')
    status = None
    for _ in range(300):
        d = jl(p.call('get_job', name=JOB)[0])
        status = (d.get('value') or {}).get('status') or d.get('status')
        if status is None:
            log(f'   get_job 回應：{json.dumps(d, ensure_ascii=False)[:500]}'); break
        if status in ('OK', 'Killed', 'TimedOut', 'Failed', 'FailedWithResults'):
            break
        time.sleep(2)
    log(f'   求解狀態：{status}')
    if status != 'OK':
        d = jl(p.call('get_job', name=JOB, include_output=True)[0])
        log(str((d.get('value') or d))[:3000]); return

    wd, _ = run(p, "print(pmx.work_directory)")
    inp = os.path.join(wd, JOB + '.inp'); frd = os.path.join(wd, JOB + '.frd')
    log(f'   結果檔：{frd}（存在={os.path.exists(frd)}）')

    log('7. 評估')
    cfgp = os.path.join(OUTDIR, 'fea_config.json')
    fea_eval.make_config_template(fea_eval.read_inp(inp), cfgp)
    cfg = json.load(open(cfgp, encoding='utf-8'))
    for k in cfg['parts']:
        cfg['parts'][k].update(display_name='V003方管', material_class='ductile', allowable=420)
    cfg.update(safety_factor_required=1.0, exclude_bc_radius=20, hotspot_cluster_distance=40, regions=[])
    json.dump(cfg, open(cfgp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    r = fea_eval.evaluate(inp, frd, cfg)
    fea_eval.write_outputs(r, os.path.join(OUTDIR, 'V003_評估結果'), cfg)

    # 端面平均 U2
    res = fea_eval.read_frd(frd)
    nodes = r['model']['nodes']
    tip = [res['DISP'][n][1] for n, c in nodes.items() if abs(c[0]) < 1e-6 and n in res['DISP']]
    d_tip = sum(tip) / len(tip)
    I = (B*H**3 - b*h**3) / 12
    G = E / (2*(1+NU))
    d_bend = F*L**3/(3*E*I)
    A_web = 2 * (B-b)/2 * H          # 兩側腹板面積（剪力主要由腹板承受）
    d_shear = F*L/(G*A_web)
    k_fe = F/abs(d_tip)
    log('8. 剛性比對')
    log(f'   FE 端面平均位移 U2 = {d_tip:.6f} mm（{len(tip)} 個節點）')
    log(f'   梁理論 彎曲 FL³/3EI = {d_bend:.6f} mm；剪切（腹板近似）FL/GA_web = {d_shear:.6f} mm；合計 {d_bend+d_shear:.6f} mm')
    log(f'   剛性 k_FE = {k_fe:.3f} N/mm；k_彎曲 = {F/d_bend:.3f}；k_彎曲+剪切 = {F/(d_bend+d_shear):.3f}')
    log(f'   差異：對彎曲 {100*(abs(d_tip)-d_bend)/d_bend:.2f}%；對彎曲+剪切 {100*(abs(d_tip)-(d_bend+d_shear))/(d_bend+d_shear):.2f}%')
    se = r['total_se']; wc = r['work_check']
    log(f'   總應變能 {se:.6f} N·mm；外力功 ½ΣF·u {wc:.6f}；½F·δ_avg {0.5*F*abs(d_tip):.6f}')
    log(f'   質量 {r["total_mass"]*1000:.4f} kg')
    with open(os.path.join(OUTDIR, 'V003_log.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG) + '\n')
    log('完成。PrePoMax 中的修改可在 編輯 → 編輯歷程 檢視或移除。')

if __name__ == '__main__':
    try:
        main()
    finally:
        with open(os.path.join(OUTDIR, 'V003_log.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(LOG) + '\n')
