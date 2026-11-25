# -*- coding: utf-8 -*-
"""
全自動組件分析：把 STEP 或 PrePoMax 模型（.pmx）放進 FEA-AI\\input\\，執行本程式（VS Code 任務「FEA: 全自動分析 input 資料夾的 STEP / .pmx」）。
--current：直接使用 PrePoMax 目前開著的模型（例如在 PrePoMax 中用匯入功能讀進的 STEP），先另存副本到 results，再照同樣流程分析。
.pmx：開啟後立即另存到 results（原檔不動），沿用模型的零件、網格、材料、截面、接合；原有分析步在副本中停用，另建本工具的分析步。
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
JUDGE = json.load(open(os.path.join(ROOT, 'rules', 'judgement.json'), encoding='utf-8'))
LOG = []
REPORT_HEAD = []
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

SNAME = 'AUTO_Step'

def input_files():
    fs = []
    for ext in ('*.stp', '*.step', '*.STP', '*.STEP', '*.pmx', '*.PMX'):
        fs += glob.glob(os.path.join(INPUT, ext))
    return sorted(set(fs), key=os.path.getmtime)

CURRENT = '--current' in sys.argv
OUTDIR = [None]

def journal_step(p):
    """從 PrePoMax 指令歷程找出最後匯入的 STEP 檔路徑（使用者在 PrePoMax 中手動匯入時用）"""
    d = jl(p.call('get_history', include_journal=True)[0])
    j = d.get('journal')
    txt = j.get('text') or j.get('content') or json.dumps(j) if isinstance(j, dict) else (j or json.dumps(d, ensure_ascii=False))
    if isinstance(j, dict) and j.get('fileName') and os.path.exists(j['fileName']):
        txt = open(j['fileName'], encoding='utf-8', errors='replace').read()
    import re as _re
    hits = _re.findall(r"""import_file\(\s*r?['"]([^'"]+\.(?:stp|step))['"]""", txt, _re.I)
    return hits[-1].replace('\\\\', '\\') if hits else None

def main():
    p = PrePoMax()
    if CURRENT:
        st0 = jl(p.call('get_status')[0]); m0 = st0.get('model', {})
        if not m0.get('isOpen'): raise Stop('PrePoMax 沒有開啟的模型')
        SRC = m0.get('fileName') or ''
        stepf = journal_step(p)
        name = os.path.splitext(os.path.basename(SRC or stepf or m0.get('name') or 'current_model'))[0]
        MODE = 'current'
    else:
        fs = input_files()
        if not fs: raise Stop(f'input 資料夾沒有 .stp/.step/.pmx：{INPUT}')
        SRC = fs[-1]; name = os.path.splitext(os.path.basename(SRC))[0]
        MODE = 'pmx' if SRC.lower().endswith('.pmx') else 'step'
        stepf = None
    OUT = os.path.join(RESULTS, name); os.makedirs(OUT, exist_ok=True); OUTDIR[0] = OUT
    log(f'# 全自動分析：{os.path.basename(SRC) if SRC else name}'); log(f'時間：{time.strftime("%Y-%m-%d %H:%M")}')
    log('輸入類型：' + {'pmx': 'PrePoMax 模型（.pmx）', 'step': 'STEP 幾何', 'current': 'PrePoMax 目前開著的模型'}[MODE])
    rep = {'source_file': SRC, 'mode': MODE, 'judgements': {}, 'flags': []}

    # ---- 0. PrePoMax 狀態 ----
    st = jl(p.call('get_status')[0]); m = st.get('model', {})
    if not st.get('isIdle') or st.get('isReadOnly'): raise Stop('PrePoMax 忙碌中或唯讀')
    discard = False
    if m.get('hasUnsavedChanges') and not CURRENT:
        fn = (m.get('fileName') or '').lower()
        md = jl(p.call('read_model')[0])
        mats = {x.get('name') if isinstance(x, dict) else str(x) for x in md.get('materials', [])}
        codes = set(json.load(open(MAT_FILE, encoding='utf-8'))['materials']) | {'S420'}
        if fn.endswith('auto_model.pmx') or (not fn and mats <= codes and not md.get('steps')):
            discard = True
        else:
            raise Stop(f'PrePoMax 目前的模型有未存檔的修改，為了不動到你的工作而停止。請先存檔或關閉該模型。（檔名={fn!r}，材料={sorted(mats)}，分析步={md.get("steps")}）')

    MATS = R.load_materials(MAT_FILE)
    DEFAULT = json.load(open(MAT_FILE, encoding='utf-8')).get('default_when_no_code')
    missing = []; matprops = {}

    if MODE == 'step':
        # ---- 1. 匯入與命名 ----
        log('\n## 1. 匯入與零件命名')
        run(p, f"pmx.new_model('ThreeD', 'MM_TON_S_C', discard_changes={discard})")
        run(p, f"pmx.save(r'{os.path.join(OUT, 'auto_model.pmx')}')")   # 標記為本工具建立的模型
        run(p, f"pmx.import_file(r'{SRC}')")
        gparts = pyj(p, "[[n, list(pmx.model.geometry.parts.get(n).get_bounding_box())] for n in pmx.model.geometry.parts.get_names()]")
        try:
            sp = step_names.read_step_parts(SRC)
            mp, how = step_names.match_parts(sp, [(n, b) for n, b in gparts])
            log(f'- STEP 實體 {len(sp)} 個，PrePoMax 零件 {len(gparts)} 個（STEP 編碼 {step_names.FILE_ENCODING.get(SRC)}）')
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
        names = list(new.values())
        need_mesh = True; existing_sec = {}
    else:
        # ---- 1. 開啟既有模型（另存副本，原檔不動） ----
        if MODE == 'pmx':
            log('\n## 1. 開啟既有 PrePoMax 模型')
            run(p, f"pmx.open(r'{SRC}', discard_changes={discard})")
            run(p, f"pmx.save(r'{os.path.join(OUT, 'auto_model.pmx')}')")
            log(f'- 已開啟 {SRC}，並立即另存為 results 資料夾的 auto_model.pmx；之後所有修改只在副本上，原檔不變')
        else:
            log('\n## 1. 使用 PrePoMax 目前開著的模型')
            run(p, f"pmx.save(r'{os.path.join(OUT, 'auto_model.pmx')}')")
            log(f'- 目前模型（{SRC or "尚未存檔"}）已另存為 results 資料夾的 auto_model.pmx；之後所有修改只在副本上'
                + ('，原本的 .pmx 檔不變' if SRC else ''))
            if stepf: log(f'- 指令歷程中匯入的 STEP：{stepf}')
        props = pyj(p, "[pmx.model.properties.model_space, pmx.model.properties.unit_system]")
        log(f'- 模型空間 {props[0]}，單位系統 {props[1]}')
        if props[1] != 'MM_TON_S_C': raise Stop(f'單位系統為 {props[1]}，本工具目前只支援 MM_TON_S_C（mm、N、MPa、t）')
        mparts = pyj(p, "list(pmx.model.parts.get_names())")
        gnames = pyj(p, "list(pmx.model.geometry.parts.get_names())")
        need_mesh = not mparts
        names = gnames if need_mesh else mparts
        log(f'- 零件（{"幾何，尚未網格" if need_mesh else "網格"}）：{", ".join(names)}')
        nsec = pyj(p, "len(pmx.model.sections.get_names())")
        if MODE == 'current' and stepf and os.path.exists(stepf) and nsec == 0 and all(n.lower().startswith('solid_part') for n in names):
            try:
                gparts = pyj(p, "[[n, list(pmx.model.geometry.parts.get(n).get_bounding_box())] for n in pmx.model.geometry.parts.get_names()]")
                mp, how = step_names.match_parts(step_names.read_step_parts(stepf), [(a, b) for a, b in gparts])
            except Exception as e:
                mp, how = None, f'{type(e).__name__}: {e}'
            if mp:
                newn = step_names.unique_names(mp)
                src_r = '\n'.join(f"pmx.model.geometry.parts.rename('{a}', '{b}')" for a, b in newn.items() if a != b)
                if not need_mesh:
                    src_r += '\n' + '\n'.join(f"pmx.model.parts.rename('{a}', '{b}')" for a, b in newn.items() if a != b)
                run(p, src_r or 'pass')
                names = [newn.get(n, n) for n in names]
                log(f'- 依匯入的 STEP 取回零件名稱（{how}）：' + '、'.join(f'{a} → {b}' for a, b in newn.items()))
            else:
                log(f'- ⚠ 無法由 STEP 取回零件名稱（{how}），沿用 {", ".join(names)}')
        secs = pyj(p, "[[s.name, s.region_type, s.region_name, s.material_name] for s in [pmx.model.sections.get(n) for n in pmx.model.sections.get_names()]]")
        existing_sec = {rn: mat for _, rt, rn, mat in secs if rt == 'PartName'}
        other = [s for s in secs if s[1] != 'PartName']
        nosec = [n for n in names if n not in existing_sec]
        if other and nosec:
            raise Stop(f'模型有以 {other[0][1]} 定義的截面（{[s[0] for s in other]}），無法判斷零件 {nosec} 的材料。請把截面改為以零件（PartName）指定後再執行')
        old_steps = pyj(p, "list(pmx.model.steps.get_names())")
        if old_steps:
            run(p, f"pmx.model.steps.deactivate({old_steps!r})")
            log(f'- 模型原有的分析步 {old_steps} 已在副本中停用（不參與本工具的計算）；本工具另建分析步 {SNAME}')
        rep['judgements']['naming'] = {'how': '沿用 PrePoMax 模型的零件名稱', 'map': {n: n for n in names}}

    # ---- 2. 材料 ----
    log('\n## 2. 材料')
    asg = {}
    if existing_sec:
        log('- 模型已指定的材料（沿用，不修改）：')
        for n in names:
            if n in existing_sec:
                mat = existing_sec[n]
                pr = pyj(p, f"{{x.property_type: x.get_values() for x in pmx.model.materials.get('{mat}').get_properties()}}")
                el = next((v for k, v in pr.items() if 'elastic' in k.lower()), {}); de = next((v for k, v in pr.items() if 'density' in k.lower()), {})
                code = next((c for c in sorted(MATS, key=len, reverse=True) if c.lower() in mat.lower() or c.lower() in n.lower()), None)
                matprops[n] = {'E': el.get('youngs_modulus'), 'nu': el.get('poissons_ratio'), 'density': de.get('density'), 'material': mat,
                               'class': MATS[code]['class'] if code else 'ductile', 'allowable': MATS[code]['allowable'] if code else None, 'code': code}
                if not matprops[n]['density']: raise Stop(f'材料 {mat} 沒有密度，無法計算質量與自重')
                log(f"  - {n}：{mat}（E {matprops[n]['E']}，ν {matprops[n]['nu']}，ρ {matprops[n]['density']}）"
                    + (f"，容許值依 {code}：{MATS[code]['allowable']} MPa" if code else '，⚠ 材料名稱/零件名稱沒有材料代號，強度利用率不計算'))
                asg[n] = mat
    todo = [n for n in names if n not in asg]
    if todo:
        log('- 依零件名稱的材料代號指定（沒有代號用預設材料；數值與出處見 rules/materials.json）：')
        a2, missing = R.assign_materials(todo, MATS)
        if missing:
            if not DEFAULT or DEFAULT not in MATS:
                raise Stop(f'以下零件名稱沒有材料代號，且 rules/materials.json 的 default_when_no_code 未設定或不在材料表中：{missing}')
            for n in missing: a2[n] = DEFAULT
        existing_mats = set(pyj(p, "list(pmx.model.materials.get_names())"))
        for code in sorted(set(a2.values())):
            if code in existing_mats: continue
            mt = MATS[code]
            run(p, f"pmx.model.materials.add('{code}', [pmx.create.elastic({mt['E']}, {mt['nu']}), pmx.create.density({mt['density']})])")
        for n in todo:
            mt = MATS[a2[n]]
            matprops[n] = {'E': mt['E'], 'nu': mt['nu'], 'density': mt['density'], 'material': a2[n], 'class': mt['class'], 'allowable': mt['allowable'], 'code': a2[n]}
            asg[n] = a2[n]
            log(f'  - {n}：{a2[n]}' + ('　**（名稱中沒有材料代號，使用預設材料）**' if n in missing else ''))
        if missing:
            log(f'- ⚠ {len(missing)}/{len(names)} 個零件使用預設材料 {DEFAULT}。剛性貢獻佔比與質量會受材料影響；'
                f'要改材料，在零件名稱加代號（{", ".join(MATS)}）後重跑，或修改 default_when_no_code。')
    rep['default_material_parts'] = missing
    rep['materials'] = {n: asg[n] for n in names}

    # ---- 3. 網格 ----
    if need_mesh:
        log('\n## 3. 網格（PrePoMax 預設網格設定，二次元素）')
        res = pyj(p, f"pmx.model.mesh.generate({names!r})")
        bad = {k: v for k, v in res.items() if v}
        if bad: raise Stop(f'網格失敗：{bad}')
    else:
        log('\n## 3. 網格（沿用模型既有網格）')
    et = pyj(p, "{n: pmx.model.parts.get_element_types(n) for n in pmx.model.parts.get_names()}")
    nn = pyj(p, "pmx.model.nodes.count")
    log(f'- 節點數 {nn}'); [log(f'  - {k}：{list(v.values())}') for k, v in et.items()]
    for n in todo:
        run(p, f"pmx.model.sections.add_solid('Sec_{n}', '{asg[n]}', pmx.create.region('PartName', '{n}'))")
    mesh_inp = os.path.join(OUT, 'mesh.inp')
    run(p, f"pmx.export_mesh({names!r}, r'{mesh_inp}')")
    model = fea_eval.read_inp(mesh_inp)
    if model.get('unsupported'): log(f"  ⚠ 匯出網格含未支援的元素型別：{model['unsupported']}")
    mesh = R.mesh_parts(model)
    masses = {n: mesh[n]['vol'] * matprops[n]['density'] for n in names}
    log('- 質量（網格體積 × 密度）：' + '、'.join(f'{n} {masses[n]*1000:.1f} kg' for n in names))
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

    # ---- 4. 零件接合 ----
    log('\n## 4. 零件接合')
    have_c = pyj(p, "list(pmx.model.constraints.get_names())") if MODE == 'pmx' else []
    have_cp = pyj(p, "list(pmx.model.contact_pairs.get_names())") if MODE == 'pmx' else []
    add_ties = not (have_c or have_cp)
    found = json.loads(run(p, f"import json\nr = pmx.model.contact_pairs.search({tol}, pair_type='Tie', group_by='ByParts')\n"
                   "print(json.dumps([[x.name, list(x.master_part_names), list(x.slave_part_names), str(x.unresolved)] for x in list(r)]))\n"
                   + ("if r.count: r.add()" if add_ties else "")))
    if add_ties:
        log(f'- PrePoMax「搜尋接觸對」（Tie，依零件分組）找到 {len(found)} 組，已加入模型：')
    else:
        log(f'- 沿用模型既有的約束 {have_c}' + (f' 與接觸對 {have_cp}' if have_cp else '') + '，不另外加入 Tie')
        if have_cp:
            log('  ⚠ 模型含接觸（非線性）：剛性貢獻佔比與修改效果估算只能當參考')
            rep['flags'].append('模型含接觸（非線性），剛性佔比只能當參考')
        log(f'- PrePoMax 搜尋到相接觸的零件對（只用於檢查零件是否都有連到支撐，不加入模型）：')
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
    if not g['ok']:
        passed = [r for r in g['candidates'] if r['pass']]
        if len(names) == 1 and passed:
            g['chosen'] = max(passed, key=lambda r: r['support_area']); g['ok'] = True
            log(f"- 單一零件沒有接合面可比較 C4，改依規則取「通過 C1～C3 中支撐面積最大」的方向（{len(passed)} 個方向通過）")
            rep['flags'].append('單一零件：重力方向依最大支撐面積選定，請確認擺放方向')
        else:
            raise Stop('重力方向無法判斷：' + g['reason'])
    ch = g['chosen']; d = ch['down']
    log(f"- **判斷：重力方向 {d}**，支撐零件 {ch['support_parts'][0]}，支撐面 {len(ch['support_faces'])} 個，總面積 {ch['support_area']:.0f} mm²")
    floating = R.connectivity(names, tie_edges, ch['support_parts'])
    if floating: raise Stop(f'以下零件沒有經由 Tie 連到支撐零件（會造成剛體運動）：{floating}')
    sup_pid = fp[ch['support_parts'][0]]['id']; sup_fids = [f for _, f in ch['support_faces']]
    rep['judgements']['gravity'] = {k: v for k, v in ch.items() if not k.startswith('_')}

    # ---- 6. 受力位置 ----
    log('\n## 6. 受力位置')
    tt = R.detect_tool_and_table(model, fp, mesh, mine, tol)
    for c in tt['candidates']:
        log(f"- 主軸候選：{c['spindle_part']} 端面 {c['nose_face']}（圓柱面佔 {c['cyl_ratio']*100:.0f}%），"
            + (f"正對 {c['table']['table_part']} 面 {c['table']['table_face']}，距離 {c['table']['gap']:.1f} mm" if c['table'] else '前方沒有正對的零件平面'))
    if tt['ok']:
        mode = 'tool'
        tc = tt['chosen']; tb = tc['table']
        log(f"- **判斷：主軸 {tc['spindle_part']}（端面 {tc['nose_face']}），工作台 {tb['table_part']}（面 {tb['table_face']}）**")
        r_nose = math.sqrt(tc['nose_area'] / math.pi)
        load_part, load_face = tc['spindle_part'], tc['nose_face']
        nose_nodes = R.nodes_on_plane(model, mesh[load_part]['nodes'], tc['axis_out'], tc['nose_center'], tol)
        gface = [f for f in fp[tb['table_part']]['faces'] if f['id'] == tb['table_face']][0]
        table_nodes = R.face_nodes_near(model, mesh[tb['table_part']]['nodes'], gface['normal'], gface['centroid'], tb['hit'], r_nose, tol)
        log(f'- 量測相對位移用節點：主軸端 {len(nose_nodes)} 個；工作台面上主軸投影圓（半徑 {r_nose:.1f} mm）內 {len(table_nodes)} 個')
        if not nose_nodes or not table_nodes: raise Stop('量測節點為 0')
        rep['judgements']['tool'] = {'spindle': load_part, 'nose_face': load_face, 'table': tb['table_part'], 'table_face': tb['table_face'], 'gap': tb['gap']}
        target = '主軸端與工作台之間的相對'
    else:
        mode = 'free'
        log(f"- 找不到主軸與工作台（{tt['reason']}），改用規則：離支撐面最遠、且未接合的自由平面為受力面")
        fe = R.detect_free_end(model, fp, mesh, mine, ch['support_faces'], tol)
        if not fe['ok']: raise Stop(fe['reason'])
        c = fe['chosen']; load_part, load_face = c['part'], c['face']
        log(f"- **判斷：受力面 {load_part} 面 {load_face}**，形心 ({', '.join(f'{v:.1f}' for v in c['centroid'])})，"
            f"距支撐面形心 {c['distance']:.1f} mm，面積 {c['area']:.0f} mm²")
        nose_nodes = R.nodes_on_plane(model, mesh[load_part]['nodes'], c['normal'], c['centroid'], tol)
        table_nodes = []
        log(f'- 量測位移用節點：受力面所在平面上 {len(nose_nodes)} 個')
        if not nose_nodes: raise Stop('量測節點為 0')
        rep['flags'].append(f'受力位置依「離支撐最遠的自由面」規則選定（{load_part} 面 {load_face}），請確認是否為實際受力位置')
        rep['judgements']['tool'] = {'free_end': load_part, 'face': load_face, 'distance': c['distance']}
        target = '受力面'

    # ---- 7. 邊界條件與負載 ----
    log('\n## 7. 邊界條件與工況')
    run(p, f"step = pmx.model.steps.add_static('{SNAME}')")
    run(p, f"step = pmx.model.steps.get('{SNAME}')\nstep.bcs.add_fixed('SUP_FIX', {sel_surf_nodes(sup_pid, sup_fids)})")
    axis = max(range(3), key=lambda k: abs(d[k]))
    variants = {'偏剛（支撐面全固定）': ['SUP_FIX']}
    if abs(abs(d[axis]) - 1) < 1e-6:
        a_id, b_id = R.soft_support_points(model, ch)
        A, B = model['nodes'][a_id], model['nodes'][b_id]
        ab = R.sub(B, A); e = [abs(ab[k]) if k != axis else -1 for k in range(3)]
        perp_axis = [k for k in range(3) if k != axis and k != max(range(3), key=lambda k: e[k])][0]
        u = ['u1', 'u2', 'u3']
        run(p, f"step = pmx.model.steps.get('{SNAME}')\n"
               f"step.bcs.add_displacement_rotation('SUP_VERT', {sel_surf_nodes(sup_pid, sup_fids)}, {u[axis]}=0)\n"
               f"step.bcs.add_displacement_rotation('SUP_A', {sel_nodes([a_id])}, " + ', '.join(f'{u[k]}=0' for k in range(3) if k != axis) + ")\n"
               f"step.bcs.add_displacement_rotation('SUP_B', {sel_nodes([b_id])}, {u[perp_axis]}=0)")
        variants['偏柔（支撐面只限垂直＋最少水平拘束）'] = ['SUP_VERT', 'SUP_A', 'SUP_B']
        log(f'- 偏柔支撐：支撐面限制 {u[axis]}；節點 {a_id} {tuple(round(v,1) for v in A)} 限制水平兩向；節點 {b_id} {tuple(round(v,1) for v in B)} 限制 {u[perp_axis]}')
    else:
        log('- 重力方向不是座標軸方向，偏柔支撐略過（只算偏剛）')
    pids = [fp[n]['id'] for n in names]
    gv = [G * d[k] for k in range(3)]
    run(p, f"step = pmx.model.steps.get('{SNAME}')\nstep.loads.add_gravity('LC0_G', pmx.create.selection_by_ids('femodel', 'parts', 'parts', {pids}), f1={gv[0]}, f2={gv[1]}, f3={gv[2]})")
    lp_pid = fp[load_part]['id']
    lcs = {'LC0 自重': ['LC0_G']}
    for k, ax in enumerate('XYZ'):
        f = [0.0, 0.0, 0.0]; f[k] = F_CUT
        src = f"step = pmx.model.steps.get('{SNAME}')\nstep.loads.add_surface_traction('LC{k+1}_N', {sel_surf(lp_pid, [load_face])}, f1={f[0]}, f2={f[1]}, f3={f[2]})"
        if mode == 'tool':
            src += f"\nstep.loads.add_surface_traction('LC{k+1}_T', {sel_surf(fp[tb['table_part']]['id'], [tb['table_face']])}, f1={-f[0]}, f2={-f[1]}, f3={-f[2]})"
            lcs[f'LC{k+1} 切削力 {ax}'] = [f'LC{k+1}_N', f'LC{k+1}_T']
        else:
            lcs[f'LC{k+1} 單位力 {ax}'] = [f'LC{k+1}_N']
        run(p, src)
    log(f'- LC0：全部零件自重，g = {gv} mm/s²')
    if mode == 'tool':
        log(f'- LC1～LC3：主軸端面 +{F_CUT:.0f} N、工作台面 −{F_CUT:.0f} N，分別沿 X、Y、Z（相對力，量測主軸與工作台間的相對剛性）')
    else:
        log(f'- LC1～LC3：受力面 +{F_CUT:.0f} N，分別沿 X、Y、Z（量測受力面相對支撐的剛性）')
    cut = [ln for ln in lcs if not ln.startswith('LC0')]

    # ---- 8. 求解 ----
    log('\n## 8. 求解')
    allb = [b for v in variants.values() for b in v]; alll = [l for v in lcs.values() for l in v]
    jobs = []
    for vi, (vn, bcs) in enumerate(variants.items()):
        for li, (ln, lds) in enumerate(lcs.items()):
            jn = f'AUTO_V{vi}_LC{li}'
            run(p, f"step = pmx.model.steps.get('{SNAME}')\nstep.bcs.deactivate({allb!r})\nstep.bcs.activate({bcs!r})\n"
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
    log('\n## 9. 評估')
    allow = {n: matprops[n]['allowable'] for n in names}
    cls = {n: matprops[n]['class'] for n in names}
    out = {}; blocks = None; up = [-v for v in d]
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
        for w in r['warnings']: log(f'  ⚠ {jn}：{w}')
        if blocks is None: blocks = R.region_blocks(r['elements'], names, JUDGE['region_divisions'])
        ese = {e['eid']: e['se'] for e in r['elements']}
        rec = {'parts': {n: {k: r['parts'][n][k] for k in ('SE_share', 'mass_share', 'eff', 'UR', 'stress_p99', 'strain_energy', 'mass')} for n in names},
               'total_se': r['total_se'], 'work': r['work_check'], 'warnings': r['warnings'],
               'blocks': {key: sum(ese.get(e, 0) for e in b['eids']) / r['total_se'] * 100 for key, b in blocks.items()}}
        if not grav:
            U = fea_eval.read_frd(frd)['DISP']; k = 'XYZ'.index(ln[-1])
            un = sum(U[i][k] for i in nose_nodes) / len(nose_nodes)
            ut = sum(U[i][k] for i in table_nodes) / len(table_nodes) if table_nodes else 0.0
            rec['delta_rel'] = un - ut; rec['k_N_per_um'] = F_CUT / (un - ut) / 1000.0 if un != ut else None
        out[(vn, ln)] = rec
    vns = list(variants)
    V0 = vns[0]

    # ---- 10. 判斷 ----
    lv = JUDGE['levels']
    def level(sh):
        for L in lv:
            if sh >= L['min_share']: return L
        return lv[-1]
    avg = {vn: {n: sum(out[(vn, ln)]['parts'][n]['SE_share'] for ln in cut) / len(cut) for n in names} for vn in vns}
    bavg = {vn: {key: sum(out[(vn, ln)]['blocks'][key] for ln in cut) / len(cut) for key in blocks} for vn in vns}
    rank_parts = {vn: sorted(names, key=lambda n: -avg[vn][n]) for vn in vns}
    rank_blocks = sorted(blocks, key=lambda k: -bavg[V0][k])
    g0 = out[(V0, 'LC0 自重')]['parts']
    alpha = JUDGE['stiffen_factor_for_estimate']
    red = 1 - 1 / alpha
    # 零件在「向上」方向的位置描述
    def vpos(key):
        b = blocks[key]['bounds']; part = key[0]
        es = [blocks[k]['bounds'] for k in blocks if k[0] == part]
        ax = max(range(3), key=lambda k: abs(up[k]))
        lo = min(x[2*ax] for x in es); hi = max(x[2*ax+1] for x in es)
        c = (b[2*ax] + b[2*ax+1]) / 2
        f = (c - lo) / (hi - lo) if hi > lo else 0.5
        if up[ax] < 0: f = 1 - f
        return '下段' if f < 1/3 else ('中段' if f < 2/3 else '上段')
    S = []
    def sl(x=''): S.append(str(x))
    sl(f'# 結構剛性評估報告：{os.path.basename(SRC) if SRC else (os.path.basename(stepf) + "（PrePoMax 中匯入）" if stepf else name)}')
    sl(f'產生時間：{time.strftime("%Y-%m-%d %H:%M")}　｜　工具：FEA-AI auto_analyze（PrePoMax 3.0.1 / CalculiX）')
    sl('\n## 一、結論摘要')
    k0 = {ln: out[(V0, ln)].get('k_N_per_um') for ln in cut}
    sl(f'- 評估對象：{target}剛性（三個方向各施加 {F_CUT:.0f} N）')
    sl('- 剛性：' + '、'.join(f"{ln[-1]} 向 {k0[ln]:.1f} N/μm" for ln in cut if k0[ln]) + f'（{V0}）')
    weak = min(cut, key=lambda ln: k0[ln] or 1e30)
    sl(f'- 最弱方向：**{weak[-1]} 向**（{k0[weak]:.1f} N/μm）')
    top = rank_blocks[:JUDGE['top_regions']]
    b1 = top[0]; b2 = top[1] if len(top) > 1 else None
    sl(f'- **最需要修改的位置：{b1[0]} {vpos(b1)}**，佔整體變形 **{bavg[V0][b1]:.1f}%**（三方向平均）；'
       f'此處剛性提高 {int(round((alpha-1)*100))}%，{target}變形約減少 {bavg[V0][b1]*red:.1f}%')
    if b2: sl(f'- 第二位置：{b2[0]} {vpos(b2)}，佔 {bavg[V0][b2]:.1f}%；剛性提高 {int(round((alpha-1)*100))}% → 變形約減少 {bavg[V0][b2]*red:.1f}%')
    p1 = rank_parts[V0][0]
    if len(names) > 1:
        p2 = rank_parts[V0][1]
        sl(f'- 零件層級：第一優先 **{p1}**（{avg[V0][p1]:.1f}%），第二優先 **{p2}**（{avg[V0][p2]:.1f}%）')
    else:
        sl(f'- 單一零件：{p1}，以下位置排名即為零件內需要修改的位置')
    if len(vns) > 1:
        # 位置比較允許 1 個百分點的差（對稱位置佔比相同時，排名順序可能互換）
        top1 = sorted(bavg[vns[1]], key=lambda k: -bavg[vns[1]][k])[:2]
        thr = bavg[V0][rank_blocks[min(1, len(rank_blocks)-1)]] - 1.0
        same = rank_parts[vns[0]][:2] == rank_parts[vns[1]][:2] and all(bavg[V0][k] >= thr for k in top1)
        sl(f'- 兩種支撐條件下前兩名{"相同 → 結論不受安裝條件影響" if same else "不同 → 結論受安裝條件影響，請以實際安裝方式確認"}')
    for n in names:
        if g0[n]['UR'] is not None and g0[n]['UR'] >= 1: sl(f'- ⚠ {n} 自重下強度利用率 {g0[n]["UR"]:.2f} ≥ 1，必須處理')
    lowe = [n for n in names if g0[n]['mass_share'] and avg[V0][n] / g0[n]['mass_share'] < JUDGE['lightweight_eff_below']
            and (g0[n]['UR'] is None or g0[n]['UR'] < JUDGE['lightweight_ur_below'])]
    if lowe: sl(f'- 可檢討減重（剛性貢獻低於質量比例，且自重強度利用率低）：{", ".join(lowe)}')
    for fl in rep['flags']: sl(f'- ⚠ {fl}')
    if missing: sl(f'- ⚠ {len(missing)} 個零件名稱沒有材料代號，使用預設材料 {DEFAULT}：{", ".join(missing)}')

    sl('\n## 二、修改位置建議總表')
    sl(f'每個零件依形狀切成格子（最長邊 {JUDGE["region_divisions"]} 等分），計算每格應變能佔整體的比例。佔比 = 該位置對{target}變形的貢獻。')
    sl(f'等級門檻（rules/judgement.json，可自行修改）：' + '；'.join(f"{L['name']} ≥ {L['min_share']}% {L['action']}" for L in lv[:-1]) + f"；{lv[-1]['name']} 其餘 {lv[-1]['action']}")
    sl('')
    sl(f'| 排名 | 零件 | 位置 | 座標範圍 X / Y / Z（mm，元素形心） | ' + ' | '.join(f'{ln[-1]} 向' for ln in cut)
       + f' | 平均佔比 | 累計 | 等級 | 剛性 +{int(round((alpha-1)*100))}% 時變形減少 |')
    sl('|' + '---|' * (9 + len(cut)))
    cum = 0
    for i, key in enumerate(top, 1):
        b = blocks[key]['bounds']; sh = bavg[V0][key]; cum += sh; L = level(sh)
        sl(f"| {i} | {key[0]} | {vpos(key)} | {b[0]:.0f}~{b[1]:.0f} / {b[2]:.0f}~{b[3]:.0f} / {b[4]:.0f}~{b[5]:.0f} | "
           + ' | '.join(f"{out[(V0, ln)]['blocks'][key]:.1f}%" for ln in cut)
           + f" | **{sh:.1f}%** | {cum:.1f}% | {L['name']} {L['action']} | {sh*red:.1f}% |")
    sl(f'\n前 {len(top)} 個位置合計佔 {cum:.1f}%（共 {len(blocks)} 個位置）。')

    sl('\n## 三、零件層級')
    for vn in vns:
        sl(f'\n### 支撐：{vn}')
        sl(f'| 工況 | {"相對" if mode == "tool" else ""}位移 (μm) | 剛性 (N/μm) | 總應變能 / 外力功 (N·mm) |'); sl('|---|---|---|---|')
        for ln in lcs:
            rr = out[(vn, ln)]
            dr = '—' if 'delta_rel' not in rr else f"{rr['delta_rel']*1000:.3f}"
            kk = '—' if not rr.get('k_N_per_um') else f"{rr['k_N_per_um']:.3f}"
            wk = '—' if rr['work'] is None else f"{rr['work']:.4g}"
            sl(f"| {ln} | {dr} | {kk} | {rr['total_se']:.4g} / {wk} |")
        sl('\n| 零件 | 材料 | 質量 kg | 質量佔比 | ' + ' | '.join(f'{ln[-1]} 向' for ln in cut) + ' | 平均剛性貢獻 | 效率比 | 自重利用率 | 等級 |')
        sl('|' + '---|' * (8 + len(cut)))
        for n in rank_parts[vn]:
            gg = out[(vn, 'LC0 自重')]['parts'][n]; ms = gg['mass_share']
            ur = '—' if gg['UR'] is None else f"{gg['UR']:.3f}"
            ef = f"{avg[vn][n]/ms:.2f}" if ms else '—'
            sl(f"| {n} | {asg[n]}{'（預設）' if n in missing else ''} | {masses[n]*1000:.1f} | {ms:.1f}% | "
               + ' | '.join(f"{out[(vn, ln)]['parts'][n]['SE_share']:.1f}%" for ln in cut)
               + f" | **{avg[vn][n]:.1f}%** | {ef} | {ur} | {level(avg[vn][n])['name']} |")

    sl('\n## 四、計算原理與驗證方法')
    sl('所有數字都能用下列公式由報告與結果檔自行驗算。')
    sl(f'1. **剛性**：k = F ÷ δ。F = {F_CUT:.0f} N；δ = 受力面節點在受力方向的平均位移' + ('（主軸端平均 − 工作台面主軸投影圓內平均）' if mode == 'tool' else '') + '。單位 N/μm。')
    sl('2. **變形能量守恆檢查**：總應變能 U 應等於外力功 ½ΣF·u（表中兩欄）；也可用 ½·F·δ 估算。')
    sl('3. **佔比（剛性貢獻）**：s_i = U_i ÷ U。U_i 為零件或位置內元素的應變能 ∫½σ:ε dV：由節點位移以元素形函數求積分點應變 ε、σ = Dε，高斯積分（與 CalculiX 元素內能 ELSE 相同，M-001 逐零件比對差 < 0.001%）。'
       '線性靜力、以力加載時 U = ½·F·δ，所以 s_i 就是該處對變形 δ 的貢獻比例。')
    sl(f'4. **修改效果估算**：該處剛性提高為 α 倍（例如 E×α 或等效加厚），變形減少約 s_i ×（1 − 1/α）；α = {alpha} → s_i × {red*100:.1f}%；α→∞（完全剛化）→ 最多減少 s_i。')
    sl('   驗證紀錄 M-001：立柱 E×1.1，預估 −3.40%，CalculiX 實算 −3.41%；主軸 E×1.1，預估 −2.30%，實算 −2.31%。')
    sl('5. **效率比** = 剛性貢獻 ÷ 質量佔比：> 1 表示單位重量承擔較多變形（補強效益高）；< 1 表示相對其重量貢獻少（減重檢討對象）。此為本工具定義的排序指標。')
    sl('6. **強度利用率** = 零件 P99 應力 ÷ 容許值（鋼 von Mises／降伏強度，鑄鐵 最大主應力／抗拉強度），只算自重工況；切削力工況為單位力，不判斷強度。')
    sl('7. **自行驗證步驟**：')
    sl('   - 在 PrePoMax 開 `auto_model.pmx`，開對應工作（例如 AUTO_V0_LC1）的結果，查受力面節點位移，算 k = F/δ，與表中比對。')
    sl('   - 佔比的獨立檢查：在該工作 .inp 的 *End step 前加 `*EL PRINT, ELSET=<零件名>, TOTALS=ONLY` 與下一行 `ELSE`，用 CalculiX 求解，.dat 中各零件 total internal energy 除以總和即為佔比。')
    sl('   - 修改效果檢查：把某零件 E 改為 1.1 倍重算，變形變化應接近第 4 點估算值。')

    sl('\n## 五、計算條件與限制')
    sl('- 接合一律用 Tie（假設接合面不分離、不滑動）；導軌、螺栓、軸承的局部剛性未建模。')
    sl('- 材料數值與出處：rules/materials.json；判斷門檻：rules/judgement.json。')
    sl('- 自動判斷過程（零件命名、材料、網格、接合、重力方向、支撐、受力位置）見附錄。')
    REPORT_HEAD[:] = S

    json.dump({'report': rep, 'results': {f'{a}|{b}': {**v, 'blocks': {'|'.join(map(str, k)): s for k, s in v['blocks'].items()}} for (a, b), v in out.items()},
               'blocks': {'|'.join(map(str, k)): {'bounds': b['bounds'], 'n_elem': len(b['eids'])} for k, b in blocks.items()}},
              open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
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
        fs = input_files()
        od = outdir or OUTDIR[0] or (os.path.join(RESULTS, os.path.splitext(os.path.basename(fs[-1]))[0]) if fs else RESULTS)
        os.makedirs(od, exist_ok=True)
        body = REPORT_HEAD + (['\n---\n\n# 附錄：自動判斷過程'] if REPORT_HEAD else []) + LOG
        open(os.path.join(od, '報告.md'), 'w', encoding='utf-8').write('\n'.join(body) + '\n')
