# -*- coding: utf-8 -*-
"""
全自動組件分析：把 STEP 放進 FEA-AI\\input\\，執行本程式（VS Code 任務「FEA: 全自動分析 input 的 STEP」）。
流程：匯入 → 依 STEP 名稱命名零件 → 材料（名稱代號）→ 網格 → Tie（PrePoMax 接合面搜尋）
      → 重力方向與支撐面（幾何判斷）→ 主軸端與工作台面（幾何判斷）
      → 兩種支撐 × 4 個工況求解 → 評估 → 報告
每一項自動判斷都寫出依據；判斷不成立就停止並說明，不猜測。
不動使用者自己的模型：PrePoMax 目前模型有未存檔的修改（且不是本工具建立的）就停止。
"""
import os, sys, json, time, shutil, glob, math
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from pmx_mcp import PrePoMax
import fea_eval, auto_rules as R, step_names

F_CUT = 1000.0        # 單位切削力（N）。線性靜力下剛性與佔比與力的大小無關
G = 9810.0            # mm/s²（MM_TON_S_C）
INPUT = os.path.join(ROOT, 'input'); RESULTS = os.path.join(ROOT, 'results')
MAT_FILE = os.path.join(ROOT, 'rules', 'materials.json')
LOG = []
def log(s=''): print(s); LOG.append(str(s))
def jl(t):
    try: return json.loads(t)
    except Exception: return {'raw': t}
class Stop(Exception): pass

def run(p, src, timeout=600):
    t, _ = p.call('run_python', source=src, timeout=timeout); d = jl(t)
    if d.get('failed') or 'raw' in d:
        log('---- PrePoMax Python 錯誤 ----'); log(d.get('error') or d.get('raw')); raise Stop('PrePoMax 指令失敗')
    return (d.get('output') or '').strip()

def pyj(p, expr):
    return json.loads(run(p, f"import json\nprint(json.dumps({expr}))"))

def sel_surf(pid, fids): return f"pmx.create.selection_by_ids('femodel', 'surfaces', 'surfaces', {{{pid}: {list(fids)}}})"
def sel_surf_nodes(pid, fids): return f"pmx.create.selection_by_ids('femodel', 'nodes', 'surfaces', {{{pid}: {list(fids)}}})"
def sel_nodes(ids): return f"pmx.create.selection_by_ids('femodel', 'nodes', 'nodes', {list(ids)})"

def main():
    steps = sorted(glob.glob(os.path.join(INPUT, '*.stp')) + glob.glob(os.path.join(INPUT, '*.step')), key=os.path.getmtime)
    if not steps: raise Stop(f'input 資料夾沒有 .stp/.step：{INPUT}')
    STEP = steps[-1]; name = os.path.splitext(os.path.basename(STEP))[0]
    OUT = os.path.join(RESULTS, name); os.makedirs(OUT, exist_ok=True)
    log(f'# 全自動分析：{os.path.basename(STEP)}'); log(f'時間：{time.strftime("%Y-%m-%d %H:%M")}')
    rep = {'step_file': STEP, 'judgements': {}}
    p = PrePoMax()

    # ---- 0. PrePoMax 狀態 ----
    st = jl(p.call('get_status')[0]); m = st.get('model', {})
    if not st.get('isIdle') or st.get('isReadOnly'): raise Stop('PrePoMax 忙碌中或唯讀')
    discard = False
    if m.get('hasUnsavedChanges'):
        fn = (m.get('fileName') or '').lower()
        md = jl(p.call('read_model')[0])
        mats = {x.get('name') if isinstance(x, dict) else str(x) for x in md.get('materials', [])}
        codes = set(json.load(open(MAT_FILE, encoding='utf-8'))['materials']) | {'S420'}
        if fn.endswith('auto_model.pmx') or (not fn and mats <= codes and not md.get('steps')):
            discard = True
        else:
            raise Stop(f'PrePoMax 目前的模型有未存檔的修改，為了不動到你的工作而停止。請先存檔或關閉該模型。（檔名={fn!r}，材料={sorted(mats)}，分析步={md.get("steps")}）')

    # ---- 1. 匯入與命名 ----
    log('\n## 1. 匯入與零件命名')
    run(p, f"pmx.new_model('ThreeD', 'MM_TON_S_C', discard_changes={discard})")
    run(p, f"pmx.save(r'{os.path.join(OUT, 'auto_model.pmx')}')")   # 標記為本工具建立的模型
    run(p, f"pmx.import_file(r'{STEP}')")
    gparts = pyj(p, "[[n, list(pmx.model.geometry.parts.get(n).get_bounding_box())] for n in pmx.model.geometry.parts.get_names()]")
    try:
        sp = step_names.read_step_parts(STEP)
        mp, how = step_names.match_parts(sp, [(n, b) for n, b in gparts])
        log(f'- STEP 實體 {len(sp)} 個，PrePoMax 零件 {len(gparts)} 個')
    except Exception as e:
        mp, how = None, f'讀取 STEP 零件名稱失敗（{type(e).__name__}: {e}）'
    if mp:
        new = step_names.unique_names(mp)
        run(p, '\n'.join(f"pmx.model.geometry.parts.rename('{a}', '{b}')" for a, b in new.items() if a != b) or 'pass')
        log(f'- 對應方式：{how}')
        for a, b in new.items(): log(f'  - {a} → {b}')
    else:
        new = {n: n for n, _ in gparts}
        log(f'- ⚠ 無法取得 CAD 零件名稱：{how}。沿用 PrePoMax 名稱 {", ".join(new)}')
    rep['judgements']['naming'] = {'how': how, 'map': new}

    # ---- 2. 材料 ----
    log('\n## 2. 材料（依零件名稱的材料代號；沒有代號的零件用預設材料。數值與出處見 rules/materials.json）')
    MATS = R.load_materials(MAT_FILE)
    DEFAULT = json.load(open(MAT_FILE, encoding='utf-8')).get('default_when_no_code')
    names = list(new.values())
    asg, missing = R.assign_materials(names, MATS)
    if missing:
        if not DEFAULT or DEFAULT not in MATS:
            raise Stop(f'以下零件名稱沒有材料代號，且 rules/materials.json 的 default_when_no_code 未設定或不在材料表中：{missing}')
        for n in missing: asg[n] = DEFAULT
    for n in names: log(f'- {n}：{asg[n]}' + ('　**（名稱中沒有材料代號，使用預設材料）**' if n in missing else ''))
    if missing:
        log(f'- ⚠ {len(missing)}/{len(names)} 個零件使用預設材料 {DEFAULT}。剛性貢獻佔比與質量會受材料影響；'
            f'要改材料，在零件名稱加代號（{", ".join(MATS)}）後重跑，或修改 default_when_no_code。')
    rep['default_material_parts'] = missing
    for code in sorted(set(asg.values())):
        mt = MATS[code]
        run(p, f"pmx.model.materials.add('{code}', [pmx.create.elastic({mt['E']}, {mt['nu']}), pmx.create.density({mt['density']})])")
    geo = {n: pyj(p, f"[pmx.model.geometry.parts.get('{n}').volume, list(pmx.model.geometry.parts.get('{n}').center_of_mass)]") for n in names}
    masses = {n: geo[n][0] * MATS[asg[n]]['density'] for n in names}
    rep['materials'] = {n: asg[n] for n in names}

    # ---- 3. 網格 ----
    log('\n## 3. 網格（PrePoMax 預設網格設定，二次元素）')
    res = pyj(p, f"pmx.model.mesh.generate({names!r})")
    bad = {k: v for k, v in res.items() if v}
    if bad: raise Stop(f'網格失敗：{bad}')
    et = pyj(p, "{n: pmx.model.parts.get_element_types(n) for n in pmx.model.parts.get_names()}")
    nn = pyj(p, "pmx.model.nodes.count")
    log(f'- 節點數 {nn}'); [log(f'  - {k}：{list(v.values())}') for k, v in et.items()]
    for n in names:
        run(p, f"pmx.model.sections.add_solid('Sec_{n}', '{asg[n]}', pmx.create.region('PartName', '{n}'))")
    mesh_inp = os.path.join(OUT, 'mesh.inp')
    run(p, f"pmx.export_mesh({names!r}, r'{mesh_inp}')")
    model = fea_eval.read_inp(mesh_inp)
    mesh = R.mesh_parts(model)
    for n in names: mesh[n]['cg'] = geo[n][1]
    topo = {'femodel': []}
    for n in names:
        t = jl(p.call('read_topology', part_names=[n], limit=240)[0])
        for pt in t.get('femodel', []):
            if pt.get('face_count', 0) > len(pt.get('faces', [])):
                log(f'  ⚠ {n} 有 {pt["face_count"]} 個面，只讀到 {len(pt["faces"])} 個（前 240 大）')
            topo['femodel'].append(pt)
    json.dump(topo, open(os.path.join(OUT, 'topology.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    fp = R.faces_by_part(topo)
    allx = [model['nodes'][i] for i in model['nodes']]
    diag = math.dist([min(x[k] for x in allx) for k in range(3)], [max(x[k] for x in allx) for k in range(3)])
    tol = max(0.05, 1e-4 * diag)
    log(f'- 幾何容差 {tol:.3f} mm（組件對角線 {diag:.0f} mm 的 1e-4，下限 0.05）')

    # ---- 4. Tie ----
    log('\n## 4. 零件接合（Tie）')
    found = json.loads(run(p, f"import json\nr = pmx.model.contact_pairs.search({tol}, pair_type='Tie', group_by='ByParts')\n"
                   "print(json.dumps([[x.name, list(x.master_part_names), list(x.slave_part_names), str(x.unresolved)] for x in list(r)]))\nr.add()"))
    log(f'- PrePoMax「搜尋接觸對」（Tie，依零件分組）找到 {len(found)} 組，已加入模型：')
    for nm, ma, sl, un in found: log(f'  - {nm}：master {ma}／slave {sl}' + (f'（未解析：{un}）' if un not in ('None', '[]', '', 'False') else ''))
    mine = R.detect_ties(model, fp, mesh, tol)
    pm_pairs = {frozenset([ma[0], sl[0]]) for _, ma, sl, _ in found if ma and sl and ma[0] != sl[0]}
    my_pairs = {frozenset((t['master_part'], t['slave_part'])) for t in mine}
    log(f'- 本程式平面判斷的接合零件對：{sorted(tuple(sorted(x)) for x in my_pairs)}')
    if my_pairs - pm_pairs: log(f'  ⚠ 平面判斷有、PrePoMax 搜尋沒有：{[tuple(x) for x in my_pairs - pm_pairs]}')
    if pm_pairs - my_pairs: log(f'  - PrePoMax 搜尋有、平面判斷沒有（例如圓柱面配合）：{[tuple(x) for x in pm_pairs - my_pairs]}')
    tie_edges = [{'master_part': list(x)[0], 'slave_part': list(x)[1]} for x in pm_pairs]

    # ---- 5. 重力方向與支撐 ----
    log('\n## 5. 重力方向與支撐面')
    g = R.detect_gravity(model, fp, mesh, masses, mine, tol)
    log('| 向下方向 | 支撐零件 | C1 單一零件 | C2 最重零件 | C3 重心在支撐範圍內（餘裕 mm） | C4 承重接合面比例 | 通過 |')
    log('|---|---|---|---|---|---|---|')
    for r in g['candidates']:
        log(f"| {r['down']} | {', '.join(r['support_parts'])} | {'是' if r['C1_single_part'] else '否'} | {'是' if r['C2_heaviest'] else '否'} | "
            f"{'是' if r['C3_stable'] else '否'}（{r['C3_margin']:.1f}） | {r['C4_bearing_ratio']:.2f} | {'✔' if r['pass'] else ''} |")
    log(f"- 組件重心 ({', '.join(f'{v:.1f}' for v in g['cg'])})，最重零件 {g['heaviest']}（{masses[g['heaviest']]*1000:.1f} kg）")
    if not g['ok']: raise Stop('重力方向無法判斷：' + g['reason'])
    ch = g['chosen']; d = ch['down']
    log(f"- **判斷：重力方向 {d}**，支撐零件 {ch['support_parts'][0]}，支撐面 {len(ch['support_faces'])} 個，總面積 {ch['support_area']:.0f} mm²")
    floating = R.connectivity(names, tie_edges, ch['support_parts'])
    if floating: raise Stop(f'以下零件沒有經由 Tie 連到支撐零件（會造成剛體運動）：{floating}')
    sup_pid = fp[ch['support_parts'][0]]['id']; sup_fids = [f for _, f in ch['support_faces']]
    rep['judgements']['gravity'] = {k: v for k, v in ch.items() if not k.startswith('_')}

    # ---- 6. 主軸端與工作台面 ----
    log('\n## 6. 切削力作用點（主軸端與工作台面）')
    tt = R.detect_tool_and_table(model, fp, mesh, mine, tol)
    for c in tt['candidates']:
        log(f"- 候選：{c['spindle_part']} 端面 {c['nose_face']}（圓柱面佔 {c['cyl_ratio']*100:.0f}%），"
            + (f"正對 {c['table']['table_part']} 面 {c['table']['table_face']}，距離 {c['table']['gap']:.1f} mm" if c['table'] else '前方沒有正對的零件平面'))
    if not tt['ok']: raise Stop(tt['reason'])
    tc = tt['chosen']; tb = tc['table']
    log(f"- **判斷：主軸 {tc['spindle_part']}（端面 {tc['nose_face']}），工作台 {tb['table_part']}（面 {tb['table_face']}）**")
    r_nose = math.sqrt(tc['nose_area'] / math.pi)
    nose_nodes = R.nodes_on_plane(model, mesh[tc['spindle_part']]['nodes'], tc['axis_out'], tc['nose_center'], tol)
    gface = [f for f in fp[tb['table_part']]['faces'] if f['id'] == tb['table_face']][0]
    table_nodes = R.face_nodes_near(model, mesh[tb['table_part']]['nodes'], gface['normal'], gface['centroid'], tb['hit'], r_nose, tol)
    log(f'- 量測相對位移用節點：主軸端 {len(nose_nodes)} 個；工作台面上主軸投影圓（半徑 {r_nose:.1f} mm）內 {len(table_nodes)} 個')
    if not nose_nodes or not table_nodes: raise Stop('量測節點為 0')
    rep['judgements']['tool'] = {'spindle': tc['spindle_part'], 'nose_face': tc['nose_face'], 'table': tb['table_part'],
                                 'table_face': tb['table_face'], 'gap': tb['gap']}

    # ---- 7. 邊界條件與負載 ----
    log('\n## 7. 邊界條件與工況')
    run(p, "step = pmx.model.steps.add_static('Step-1')")
    run(p, f"step = pmx.model.steps.get('Step-1')\nstep.bcs.add_fixed('SUP_FIX', {sel_surf_nodes(sup_pid, sup_fids)})")
    axis = max(range(3), key=lambda k: abs(d[k]))
    variants = {'偏剛（支撐面全固定）': ['SUP_FIX']}
    if abs(abs(d[axis]) - 1) < 1e-6:
        a_id, b_id = R.soft_support_points(model, ch)
        A, B = model['nodes'][a_id], model['nodes'][b_id]
        ab = R.sub(B, A); e = [abs(ab[k]) if k != axis else -1 for k in range(3)]
        # B 只限制與 AB 垂直、且在支撐平面內的分量中最大的那一軸
        perp_axis = [k for k in range(3) if k != axis and k != max(range(3), key=lambda k: e[k])][0]
        u = ['u1', 'u2', 'u3']
        run(p, f"step = pmx.model.steps.get('Step-1')\n"
               f"step.bcs.add_displacement_rotation('SUP_VERT', {sel_surf_nodes(sup_pid, sup_fids)}, {u[axis]}=0)\n"
               f"step.bcs.add_displacement_rotation('SUP_A', {sel_nodes([a_id])}, " + ', '.join(f'{u[k]}=0' for k in range(3) if k != axis) + ")\n"
               f"step.bcs.add_displacement_rotation('SUP_B', {sel_nodes([b_id])}, {u[perp_axis]}=0)")
        variants['偏柔（支撐面只限垂直＋最少水平拘束）'] = ['SUP_VERT', 'SUP_A', 'SUP_B']
        log(f'- 偏柔支撐：支撐面限制 {u[axis]}；節點 {a_id} {tuple(round(v,1) for v in A)} 限制水平兩向；節點 {b_id} {tuple(round(v,1) for v in B)} 限制 {u[perp_axis]}')
    else:
        log('- 重力方向不是座標軸方向，偏柔支撐略過（只算偏剛）')
    pids = [fp[n]['id'] for n in names]
    gv = [G * d[k] for k in range(3)]
    run(p, f"step = pmx.model.steps.get('Step-1')\nstep.loads.add_gravity('LC0_G', pmx.create.selection_by_ids('femodel', 'parts', 'parts', {pids}), f1={gv[0]}, f2={gv[1]}, f3={gv[2]})")
    sp_pid, tb_pid = fp[tc['spindle_part']]['id'], fp[tb['table_part']]['id']
    lcs = {'LC0 自重': ['LC0_G']}
    for k, ax in enumerate('XYZ'):
        f = [0.0, 0.0, 0.0]; f[k] = F_CUT
        run(p, f"step = pmx.model.steps.get('Step-1')\n"
               f"step.loads.add_surface_traction('LC{k+1}_N', {sel_surf(sp_pid, [tc['nose_face']])}, f1={f[0]}, f2={f[1]}, f3={f[2]})\n"
               f"step.loads.add_surface_traction('LC{k+1}_T', {sel_surf(tb_pid, [tb['table_face']])}, f1={-f[0]}, f2={-f[1]}, f3={-f[2]})")
        lcs[f'LC{k+1} 切削力 {ax}'] = [f'LC{k+1}_N', f'LC{k+1}_T']
    log(f'- LC0：全部零件自重，g = {gv} mm/s²')
    log(f'- LC1～LC3：主軸端面 +{F_CUT:.0f} N、工作台面 −{F_CUT:.0f} N，分別沿 X、Y、Z（相對力，量測主軸與工作台間的相對剛性）')

    # ---- 8. 求解 ----
    log('\n## 8. 求解')
    allb = [b for v in variants.values() for b in v]; alll = [l for v in lcs.values() for l in v]
    jobs = []
    for vi, (vn, bcs) in enumerate(variants.items()):
        for li, (ln, lds) in enumerate(lcs.items()):
            jn = f'AUTO_V{vi}_LC{li}'
            run(p, f"step = pmx.model.steps.get('Step-1')\nstep.bcs.deactivate({allb!r})\nstep.bcs.activate({bcs!r})\n"
                   f"step.loads.deactivate({alll!r})\nstep.loads.activate({lds!r})\n"
                   f"if not pmx.jobs.contains('{jn}'): pmx.jobs.add('{jn}')")
            p.call('run_job', name=jn); t0 = time.time(); status = None
            while True:
                dd = jl(p.call('get_job', name=jn)[0]); status = (dd.get('value') or {}).get('status')
                if status in ('OK', 'Killed', 'TimedOut', 'Failed', 'FailedWithResults'): break
                time.sleep(3)
            log(f'- {jn}：{vn} / {ln} → {status}（{time.time()-t0:.0f} 秒）')
            if status != 'OK':
                dd = jl(p.call('get_job', name=jn, include_output=True)[0]); log(str(dd.get('value') or dd)[-2000:]); raise Stop('求解失敗')
            jobs.append((vn, ln, jn))
    wd = run(p, "print(pmx.work_directory)")
    for _, _, jn in jobs:
        for ext in ('.inp', '.frd'): shutil.copy2(os.path.join(wd, jn + ext), os.path.join(OUT, jn + ext))
    try:
        run(p, f"pmx.save(r'{os.path.join(OUT, 'auto_model.pmx')}')")
    except Stop:
        log('  ⚠ 模型存檔失敗（不影響結果）')

    # ---- 9. 評估 ----
    log('\n## 9. 結果')
    allow = {n: MATS[asg[n]]['allowable'] for n in names}
    cls = {n: MATS[asg[n]]['class'] for n in names}
    out = {}
    for vn, ln, jn in jobs:
        inp, frd = os.path.join(OUT, jn + '.inp'), os.path.join(OUT, jn + '.frd')
        with open(frd, 'rb') as fb:
            if b'\x00' in fb.read(65536): raise Stop('.frd 為二進位格式，請把 PrePoMax 結果輸出改為 ASCII')
        jm = fea_eval.read_inp(inp)
        for i in (nose_nodes[:3] + table_nodes[:3]):
            if math.dist(jm['nodes'][i], model['nodes'][i]) > tol: raise Stop('求解檔與匯出網格的節點編號不一致')
        grav = ln.startswith('LC0')
        cfg = {'parts': {n: {'display_name': n, 'material_class': cls[n], 'allowable': allow[n] if grav else None, 'moving_axis': None} for n in names},
               'safety_factor_required': 1.0, 'weights': {'strength': 0.5, 'stiffness': 0.5},
               'exclude_bc_radius': None, 'hotspot_top_percent': 5, 'hotspot_cluster_distance': 0.05 * diag, 'regions': []}
        r = fea_eval.evaluate(inp, frd, cfg)
        fea_eval.write_outputs(r, os.path.join(OUT, jn + '_評估'), cfg)
        rec = {'parts': {n: {k: r['parts'][n][k] for k in ('SE_share', 'mass_share', 'eff', 'UR', 'stress_p99', 'strain_energy', 'mass')} for n in names},
               'total_se': r['total_se'], 'work': r['work_check'], 'warnings': r['warnings'],
               'hotspots': {n: [{'centroid': h['centroid'], 'share': h['share_of_part']} for h in r['hotspots'][n][:2]] for n in names}}
        if not grav:
            U = fea_eval.read_frd(frd)['DISP']; k = 'XYZ'.index(ln[-1])
            un = sum(U[i][k] for i in nose_nodes) / len(nose_nodes); ut = sum(U[i][k] for i in table_nodes) / len(table_nodes)
            rec['delta_rel'] = un - ut; rec['k_N_per_um'] = F_CUT / (un - ut) / 1000.0 if un != ut else None
        out[(vn, ln)] = rec

    # ---- 10. 判斷 ----
    for vn in variants:
        log(f'\n### 支撐：{vn}')
        log('| 工況 | 相對位移 (μm) | 相對剛性 (N/μm) | 總應變能 / 外力功 |'); log('|---|---|---|---|')
        for ln in lcs:
            rr = out[(vn, ln)]
            dr = '—' if 'delta_rel' not in rr else f"{rr['delta_rel']*1000:.3f}"
            kk = '—' if not rr.get('k_N_per_um') else f"{rr['k_N_per_um']:.3f}"
            wk = '—' if rr['work'] is None else f"{rr['work']:.4g}"
            log(f"| {ln} | {dr} | {kk} | {rr['total_se']:.4g} / {wk} |")
        log('\n| 零件 | 材料 | 質量 kg | 質量佔比 | 剛性貢獻 LC1 X | LC2 Y | LC3 Z | 平均 | 效率比 | 自重利用率 |')
        log('|---|---|---|---|---|---|---|---|---|---|')
        avg = {}
        for n in names:
            sh = [out[(vn, ln)]['parts'][n]['SE_share'] for ln in lcs if not ln.startswith('LC0')]
            avg[n] = sum(sh) / len(sh)
        for n in sorted(names, key=lambda n: -avg[n]):
            g0 = out[(vn, 'LC0 自重')]['parts'][n]
            ms = g0['mass_share']
            shs = ' | '.join(f"{out[(vn, ln)]['parts'][n]['SE_share']:.1f}%" for ln in lcs if not ln.startswith('LC0'))
            ur = '—' if g0['UR'] is None else f"{g0['UR']:.3f}"
            ef = f"{avg[n]/ms:.2f}" if ms else '—'
            log(f"| {n} | {asg[n]}{'（預設）' if n in missing else ''} | {masses[n]*1000:.1f} | {ms:.1f}% | {shs} | **{avg[n]:.1f}%** | {ef} | {ur} |")
        rep.setdefault('ranking', {})[vn] = sorted(names, key=lambda n: -avg[n])
        rep.setdefault('avg_share', {})[vn] = avg
    log('\n## 10. 結論')
    rk = rep['ranking']; vns = list(rk)
    first, second = rk[vns[0]][0], rk[vns[0]][1]
    log(f'- 剛性貢獻（切削力 3 方向平均）第一優先：**{first}**（{rep["avg_share"][vns[0]][first]:.1f}%），第二優先：**{second}**（{rep["avg_share"][vns[0]][second]:.1f}%）')
    if len(vns) > 1:
        same = rk[vns[0]][:2] == rk[vns[1]][:2]
        log(f'- 兩種支撐的前兩名{"相同 → 結論不受安裝條件影響" if same else "不同 → 結論取決於安裝條件，需實際安裝方式確認"}：'
            + '；'.join(f'{v}：{rk[v][0]}、{rk[v][1]}' for v in vns))
        rep['support_agree'] = same
    g0 = out[(vns[0], 'LC0 自重')]['parts']
    for n in names:
        if g0[n]['UR'] is not None and g0[n]['UR'] >= 1: log(f'- ⚠ {n} 自重下強度利用率 {g0[n]["UR"]:.2f} ≥ 1，必須處理')
    lowe = [n for n in names if rep['avg_share'][vns[0]][n] / g0[n]['mass_share'] < 1]
    if lowe: log(f'- 剛性貢獻低於質量比例（效率比 < 1，可優先檢討減重）：{", ".join(lowe)}')
    for n in (first, second):
        hs = out[(vns[0], 'LC1 切削力 X')]['hotspots'][n]
        log(f'- {n} 應變能集中區（LC1 X，前 2）：' + '；'.join(f"({', '.join(f'{v:.0f}' for v in h['centroid'])}) 佔該零件 {h['share']:.1f}%" for h in hs))
    log('\n## 計算依據與限制')
    log('- 剛性貢獻 = 各零件應變能 ÷ 總應變能（線性靜力、以力加載時等於對主軸–工作台相對柔度的貢獻佔比）。效率比 = 剛性貢獻 ÷ 質量佔比（本工具定義的排序指標）。')
    log('- 強度利用率只算自重工況（P99 應力 ÷ 容許值）；切削力工況使用 1000 N 單位力，只用於剛性，不判斷強度。')
    log('- 接合一律用 Tie（假設接合面不分離、不滑動）；導軌、螺栓的局部剛性未建模。')
    log('- 材料數值與出處見 rules/materials.json。')
    json.dump({'report': rep, 'results': {f'{a}|{b}': v for (a, b), v in out.items()}}, open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=str)
    try:
        run(p, f"pmx.view.save_image(r'{os.path.join(OUT, 'model.png')}')")
    except Stop: pass
    log(f'\n結果資料夾：{OUT}')
    return OUT

if __name__ == '__main__':
    outdir = None
    try:
        outdir = main()
    except Stop as e:
        log(f'\n**停止：{e}**')
    finally:
        steps = sorted(glob.glob(os.path.join(INPUT, '*.stp')) + glob.glob(os.path.join(INPUT, '*.step')), key=os.path.getmtime)
        od = outdir or (os.path.join(RESULTS, os.path.splitext(os.path.basename(steps[-1]))[0]) if steps else RESULTS)
        os.makedirs(od, exist_ok=True)
        open(os.path.join(od, '報告.md'), 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
