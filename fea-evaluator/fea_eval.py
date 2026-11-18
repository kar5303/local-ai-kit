# -*- coding: utf-8 -*-
"""
FEA 結構評估工具 v0.1（PrePoMax / CalculiX）
讀取 .inp + .frd → 依零件計算強度、應變能（剛性貢獻）、質量／重心／慣性 → 排名與熱區 → 報告

只用 Python 標準函式庫，不需安裝套件。
用法：
    python fea_eval.py Analysis-1.inp Analysis-1.frd [config.json]
第一次執行若沒有 config.json，會產生一份範本（fea_config.json），請填好再執行。
"""
import sys, os, json, math, csv, re
from collections import defaultdict

# ----------------------------------------------------------------------------
# 1. 讀取 .inp
# ----------------------------------------------------------------------------

def _keyword(line):
    return line.split(',')[0].strip().upper()

def _params(line):
    out = {}
    for p in line.split(',')[1:]:
        if '=' in p:
            k, v = p.split('=', 1)
            out[k.strip().upper()] = v.strip()
        elif p.strip():
            out[p.strip().upper()] = True
    return out

def read_inp(path):
    model = {
        'nodes': {}, 'elements': {}, 'elem_type': {}, 'elsets': defaultdict(list),
        'nsets': defaultdict(list), 'materials': {}, 'sections': [], 'boundary_sets': [],
        'gravity': [], 'unit_system': None, 'part_sets': [],
    }
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = [l.rstrip('\n') for l in f]
    i = 0
    mode = None
    cur = {}
    pending_elem = []
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        i += 1
        if s.startswith('**'):
            m = re.search(r'Unit system:\s*(\S+)', s)
            continue
        m = re.search(r'Unit system:\s*(\S+)', s)
        if m:
            model['unit_system'] = m.group(1)
        if not s:
            continue
        if s.startswith('*'):
            kw = _keyword(s)
            pr = _params(s)
            mode = None
            if kw == '*NODE':
                mode = 'node'
            elif kw == '*ELEMENT':
                mode = 'elem'
                cur = {'type': pr.get('TYPE', '').upper(), 'elset': pr.get('ELSET')}
                pending_elem = []
            elif kw == '*ELSET':
                mode = 'elset'
                cur = {'name': pr['ELSET'], 'generate': 'GENERATE' in pr}
            elif kw == '*NSET':
                mode = 'nset'
                cur = {'name': pr['NSET'], 'generate': 'GENERATE' in pr}
            elif kw == '*MATERIAL':
                cur = {'name': pr['NAME']}
                model['materials'][cur['name']] = {}
                mode = None
            elif kw == '*DENSITY':
                mode = 'density'
            elif kw == '*ELASTIC':
                mode = 'elastic'
            elif kw == '*SOLID SECTION':
                model['sections'].append({'elset': pr.get('ELSET'), 'material': pr.get('MATERIAL')})
            elif kw == '*BOUNDARY':
                mode = 'boundary'
            elif kw == '*DLOAD':
                mode = 'dload'
            continue
        parts = [p.strip() for p in s.split(',') if p.strip() != '']
        if mode == 'node':
            model['nodes'][int(parts[0])] = tuple(float(x) for x in parts[1:4])
        elif mode == 'elem':
            pending_elem += parts
            nn = {'C3D20': 20, 'C3D20R': 20, 'C3D10': 10, 'C3D4': 4, 'C3D8': 8, 'C3D8R': 8, 'C3D8I': 8}.get(cur['type'])
            if nn is None:
                pending_elem = []
                continue
            if len(pending_elem) >= nn + 1:
                eid = int(pending_elem[0])
                model['elements'][eid] = [int(x) for x in pending_elem[1:nn + 1]]
                model['elem_type'][eid] = cur['type']
                if cur['elset']:
                    model['elsets'][cur['elset']].append(eid)
                    if cur['elset'] not in model['part_sets']:
                        model['part_sets'].append(cur['elset'])
                pending_elem = []
        elif mode in ('elset', 'nset'):
            target = model['elsets'] if mode == 'elset' else model['nsets']
            if cur['generate']:
                a, b, c = (int(x) for x in (parts + ['1'])[:3])
                target[cur['name']] += list(range(a, b + 1, c))
            else:
                for p in parts:
                    if re.fullmatch(r'-?\d+', p):
                        target[cur['name']].append(int(p))
                    else:  # 參照其他 set
                        target[cur['name']] += target.get(p, [])
        elif mode == 'density':
            model['materials'][cur['name']]['density'] = float(parts[0])
            mode = None
        elif mode == 'elastic':
            model['materials'][cur['name']]['E'] = float(parts[0])
            model['materials'][cur['name']]['nu'] = float(parts[1])
            mode = None
        elif mode == 'boundary':
            if not re.fullmatch(r'\d+', parts[0]):
                model['boundary_sets'].append(parts[0])
            else:
                model['nsets']['_BC_NODES_'].append(int(parts[0]))
        elif mode == 'dload':
            if len(parts) >= 6 and parts[1].upper() == 'GRAV':
                model['gravity'].append({'set': parts[0], 'g': float(parts[2]),
                                         'dir': tuple(float(x) for x in parts[3:6])})
    return model

# ----------------------------------------------------------------------------
# 2. 讀取 .frd（ASCII）
# ----------------------------------------------------------------------------

def read_frd(path):
    """回傳 {block_name: {node_id: [values...]}}，只取最後一個增量步。"""
    results = {}
    with open(path, encoding='utf-8', errors='replace') as f:
        name = None
        data = None
        ncomp = 0
        for line in f:
            if line.startswith(' -4'):
                name = line[5:13].strip()
                ncomp = int(line[13:18])
                data = {}
            elif line.startswith(' -1') and data is not None:
                nid = int(line[3:13])
                vals = []
                pos = 13
                raw = line.rstrip('\n')
                for _ in range(ncomp):
                    chunk = raw[pos:pos + 12]
                    if not chunk.strip():
                        break   # 如 DISP 宣告 4 個分量，但 ALL 不會寫出
                    vals.append(float(chunk))
                    pos += 12
                data[nid] = vals
            elif line.startswith(' -3') and data is not None:
                results[name] = data   # 後面的增量會覆蓋前面
                data = None
    return results

# ----------------------------------------------------------------------------
# 3. 形函數與積分點
# ----------------------------------------------------------------------------

_HEX20_NAT = [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1),
              (0,-1,-1),(1,0,-1),(0,1,-1),(-1,0,-1),(0,-1,1),(1,0,1),(0,1,1),(-1,0,1),
              (-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)]

def _N_hex20(x, y, z):
    N = []
    for (a, b, c) in _HEX20_NAT:
        if a != 0 and b != 0 and c != 0:
            N.append(0.125 * (1 + a*x) * (1 + b*y) * (1 + c*z) * (a*x + b*y + c*z - 2))
        elif a == 0:
            N.append(0.25 * (1 - x*x) * (1 + b*y) * (1 + c*z))
        elif b == 0:
            N.append(0.25 * (1 + a*x) * (1 - y*y) * (1 + c*z))
        else:
            N.append(0.25 * (1 + a*x) * (1 + b*y) * (1 - z*z))
    return N

def _N_hex8(x, y, z):
    return [0.125 * (1 + a*x) * (1 + b*y) * (1 + c*z) for (a, b, c) in _HEX20_NAT[:8]]

def _N_tet10(x, y, z):
    L = [1 - x - y - z, x, y, z]
    N = [L[i] * (2*L[i] - 1) for i in range(4)]
    for (i, j) in [(0,1),(1,2),(2,0),(0,3),(1,3),(2,3)]:
        N.append(4 * L[i] * L[j])
    return N

def _N_tet4(x, y, z):
    return [1 - x - y - z, x, y, z]

def _gauss_hex(n):
    if n == 3:
        p = [-math.sqrt(0.6), 0.0, math.sqrt(0.6)]; w = [5/9, 8/9, 5/9]
    else:
        p = [-1/math.sqrt(3), 1/math.sqrt(3)]; w = [1.0, 1.0]
    return [((p[i], p[j], p[k]), w[i]*w[j]*w[k]) for i in range(n) for j in range(n) for k in range(n)]

def _gauss_tet():
    # Keast 11 點（4 次精度）
    pts = [((0.25, 0.25, 0.25), -0.01315555555)]
    a, b = 0.0714285714285714, 0.785714285714286
    for p in [(a,a,a),(b,a,a),(a,b,a),(a,a,b)]:
        pts.append((p, 0.00762222222222222))
    a, b = 0.399403576166799, 0.100596423833201
    for p in [(a,a,b),(a,b,a),(a,b,b),(b,a,a),(b,a,b),(b,b,a)]:
        pts.append((p, 0.0248888888888889))
    return pts

def _N_wedge15(x, y, z):
    """15 節點楔形（CalculiX/Abaqus 節點順序）：x,y 為三角形座標，z 為 -1~1"""
    L = [1 - x - y, x, y]
    N = []
    for c in (-1, 1):
        for i in range(3):
            N.append(0.5*L[i]*(2*L[i]-1)*(1 + c*z) - 0.5*L[i]*(1 - z*z))
    for c in (-1, 1):
        for (i, j) in [(0, 1), (1, 2), (2, 0)]:
            N.append(2*L[i]*L[j]*(1 + c*z))
    for i in range(3):
        N.append(L[i]*(1 - z*z))
    return N

def _N_wedge6(x, y, z):
    L = [1 - x - y, x, y]
    return [0.5*L[i]*(1 - z) for i in range(3)] + [0.5*L[i]*(1 + z) for i in range(3)]

def _gauss_wedge():
    tri = [((1/6, 1/6), 1/6), ((2/3, 1/6), 1/6), ((1/6, 2/3), 1/6)]
    p = [-math.sqrt(0.6), 0.0, math.sqrt(0.6)]; w = [5/9, 8/9, 5/9]
    return [((a, b, p[k]), wt*w[k]) for (a, b), wt in tri for k in range(3)]

ELEM_INFO = {
    'C3D20': (_N_hex20, _gauss_hex(3)), 'C3D20R': (_N_hex20, _gauss_hex(3)),
    'C3D8': (_N_hex8, _gauss_hex(2)), 'C3D8R': (_N_hex8, _gauss_hex(2)), 'C3D8I': (_N_hex8, _gauss_hex(2)),
    'C3D10': (_N_tet10, _gauss_tet()), 'C3D4': (_N_tet4, _gauss_tet()),
    'C3D15': (_N_wedge15, _gauss_wedge()), 'C3D6': (_N_wedge6, _gauss_wedge()),
}

def _dN(Nfun, x, y, z, h=1e-6):
    fx1, fx0 = Nfun(x+h, y, z), Nfun(x-h, y, z)
    fy1, fy0 = Nfun(x, y+h, z), Nfun(x, y-h, z)
    fz1, fz0 = Nfun(x, y, z+h), Nfun(x, y, z-h)
    return [((fx1[i]-fx0[i])/(2*h), (fy1[i]-fy0[i])/(2*h), (fz1[i]-fz0[i])/(2*h)) for i in range(len(fx1))]

def _det3(m):
    return (m[0][0]*(m[1][1]*m[2][2]-m[1][2]*m[2][1]) - m[0][1]*(m[1][0]*m[2][2]-m[1][2]*m[2][0])
            + m[0][2]*(m[1][0]*m[2][1]-m[1][1]*m[2][0]))

# ----------------------------------------------------------------------------
# 4. 應力相關計算
# ----------------------------------------------------------------------------

def mises(s):
    sxx, syy, szz, sxy, syz, szx = s
    return math.sqrt(0.5*((sxx-syy)**2 + (syy-szz)**2 + (szz-sxx)**2) + 3*(sxy**2 + syz**2 + szx**2))

def principal_max(s):
    """最大主應力（三次方程式解析解）。"""
    sxx, syy, szz, sxy, syz, szx = s
    I1 = sxx + syy + szz
    I2 = sxx*syy + syy*szz + szz*sxx - sxy**2 - syz**2 - szx**2
    I3 = sxx*syy*szz + 2*sxy*syz*szx - sxx*syz**2 - syy*szx**2 - szz*sxy**2
    p = I1**2/3 - I2
    q = 2*I1**3/27 - I1*I2/3 + I3
    if p <= 1e-30:
        return I1/3
    r = math.sqrt(p/3)
    arg = max(-1.0, min(1.0, q/(2*r**3)))
    phi = math.acos(arg)/3
    return I1/3 + 2*r*math.cos(phi)

def energy_density(s, E, nu):
    """線彈性等向材料的應變能密度 W = ½ σ:ε，以應力和材料常數計算。"""
    sxx, syy, szz, sxy, syz, szx = s
    return ((sxx**2 + syy**2 + szz**2)/(2*E) - nu/E*(sxx*syy + syy*szz + szz*sxx)
            + (1+nu)/E*(sxy**2 + syz**2 + szx**2))

# ----------------------------------------------------------------------------
# 5. 主評估流程
# ----------------------------------------------------------------------------

def percentile(vals, p):
    if not vals:
        return 0.0
    v = sorted(vals)
    k = (len(v)-1) * p/100
    f = math.floor(k); c = math.ceil(k)
    return v[f] if f == c else v[f] + (v[c]-v[f])*(k-f)

UNIT_INFO = {
    'MM_TON_S_C': {'stress': 'MPa', 'energy': 'N·mm', 'mass_to_kg': 1000.0, 'length': 'mm', 'g_std': 9810.0},
    'MM_TON_S': {'stress': 'MPa', 'energy': 'N·mm', 'mass_to_kg': 1000.0, 'length': 'mm', 'g_std': 9810.0},
    'M_KG_S_C': {'stress': 'Pa', 'energy': 'J', 'mass_to_kg': 1.0, 'length': 'm', 'g_std': 9.81},
    'M_KG_S': {'stress': 'Pa', 'energy': 'J', 'mass_to_kg': 1.0, 'length': 'm', 'g_std': 9.81},
}

def part_materials(model):
    """零件（*Element 的 ELSET）→ 材料，依元素是否屬於 section 的 elset 判斷。"""
    out = {}
    for p in model['part_sets']:
        es = set(model['elsets'][p])
        for sec in model['sections']:
            if es & set(model['elsets'].get(sec['elset'], [])):
                out[p] = sec['material']
                break
    return out

def make_config_template(model, path):
    part_sets = model['part_sets']
    mats = part_materials(model)
    cfg = {
        "_說明": "每個零件一個項目。material_class: ductile(結構鋼，用 Mises) / brittle(灰鑄鐵，用最大主應力)。allowable: 鋼填降伏強度、鑄鐵填抗拉強度（依公司標準）。",
        "parts": {
            s: {"display_name": s, "material_class": "ductile", "allowable": None,
                "moving_axis": None, "_inp_material": mats.get(s)} for s in part_sets
        },
        "safety_factor_required": None,
        "weights": {"strength": 0.5, "stiffness": 0.5},
        "exclude_bc_radius": None,
        "hotspot_top_percent": 5,
        "hotspot_cluster_distance": None,
        "regions": [
            {"_例": "底座導軌安裝面", "part": "BASE", "xmin": 0, "xmax": 0, "ymin": 0, "ymax": 0, "zmin": 0, "zmax": 0}
        ]
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def evaluate(inp_path, frd_path, cfg):
    model = read_inp(inp_path)
    res = read_frd(frd_path)
    units = UNIT_INFO.get(model['unit_system'] or '', {'stress': '(模型單位)', 'energy': '(模型單位)', 'mass_to_kg': None, 'length': '', 'g_std': None})
    nodes = model['nodes']
    S = res.get('STRESS')
    U = res.get('DISP')
    if S is None:
        raise SystemExit('frd 裡沒有 STRESS，請在 Field Output 勾選 S')
    warnings = []

    # 重力單位檢查（只檢查，不修改）
    for g in model['gravity']:
        if units.get('g_std') and abs(g['g'] - units['g_std']) / units['g_std'] > 0.01:
            warnings.append(f"重力加速度設定為 {g['g']}，單位系統 {model['unit_system']} 的標準重力是 {units['g_std']}。請確認輸入是否正確。")

    # 零件 → 元素、材料
    part_cfg = cfg['parts']
    sec_mat = part_materials(model)
    part_elems = {}
    for p in part_cfg:
        if p not in model['elsets']:
            warnings.append(f"設定檔中的零件 {p} 在 .inp 找不到對應的 Element Set")
            continue
        part_elems[p] = model['elsets'][p]

    # 邊界條件節點（排除熱點用）
    bc_nodes = set()
    for sname in model['boundary_sets']:
        bc_nodes.update(model['nsets'].get(sname, []))
    bc_nodes.update(model['nsets'].get('_BC_NODES_', []))
    bc_pts = [nodes[n] for n in bc_nodes if n in nodes]
    rad = cfg.get('exclude_bc_radius')

    def near_bc(p):
        if not rad or not bc_pts:
            return False
        r2 = rad * rad
        return any((p[0]-q[0])**2 + (p[1]-q[1])**2 + (p[2]-q[2])**2 <= r2 for q in bc_pts)

    parts_out = {}
    elem_records = []
    for p, eids in part_elems.items():
        mat_name = sec_mat.get(p)
        mat = model['materials'].get(mat_name, {})
        E, nu, rho = mat.get('E'), mat.get('nu'), mat.get('density', 0.0)
        if E is None:
            warnings.append(f"零件 {p} 的材料 {mat_name} 沒有彈性係數，無法計算應變能")
        pc = part_cfg[p]
        brittle = pc.get('material_class') == 'brittle'

        vol = mass = se = 0.0
        mx = my = mz = 0.0
        Ixx = Iyy = Izz = Ixy = Iyz = Izx = 0.0  # 對原點的二次矩（質量）
        part_nodes = set()
        for eid in eids:
            et = model['elem_type'][eid]
            if et not in ELEM_INFO:
                warnings.append(f"元素型別 {et} 尚未支援（元素 {eid}）")
                continue
            Nf, gp = ELEM_INFO[et]
            conn = model['elements'][eid]
            part_nodes.update(conn)
            xyz = [nodes[n] for n in conn]
            st = [S.get(n, [0]*6) for n in conn]
            ev = ee = 0.0
            cx = cy = cz = 0.0
            for (pt, w) in gp:
                N = Nf(*pt)
                dN = _dN(Nf, *pt)
                J = [[sum(dN[i][a]*xyz[i][b] for i in range(len(N))) for b in range(3)] for a in range(3)]
                dV = _det3(J) * w
                X = [sum(N[i]*xyz[i][k] for i in range(len(N))) for k in range(3)]
                sg = [sum(N[i]*st[i][k] for i in range(len(N))) for k in range(6)]
                ev += dV
                if E:
                    ee += energy_density(sg, E, nu) * dV
                cx += X[0]*dV; cy += X[1]*dV; cz += X[2]*dV
                Ixx += rho*(X[1]**2 + X[2]**2)*dV; Iyy += rho*(X[0]**2 + X[2]**2)*dV; Izz += rho*(X[0]**2 + X[1]**2)*dV
                Ixy += rho*X[0]*X[1]*dV; Iyz += rho*X[1]*X[2]*dV; Izx += rho*X[2]*X[0]*dV
            vol += ev; se += ee; mass += rho*ev
            mx += rho*cx; my += rho*cy; mz += rho*cz
            cen = (cx/ev, cy/ev, cz/ev) if ev else (0, 0, 0)
            elem_records.append({'part': p, 'eid': eid, 'centroid': cen, 'vol': ev, 'se': ee,
                                 'sed': ee/ev if ev else 0})

        cg = (mx/mass, my/mass, mz/mass) if mass else (None, None, None)
        if mass:
            # 平移到重心（平行軸定理）
            Icg = {
                'Ixx': Ixx - mass*(cg[1]**2 + cg[2]**2), 'Iyy': Iyy - mass*(cg[0]**2 + cg[2]**2),
                'Izz': Izz - mass*(cg[0]**2 + cg[1]**2),
                'Ixy': -(Ixy - mass*cg[0]*cg[1]), 'Iyz': -(Iyz - mass*cg[1]*cg[2]), 'Izx': -(Izx - mass*cg[2]*cg[0]),
            }
        else:
            Icg = {}

        # 節點應力
        evals, evals_all, max_info = [], [], None
        for n in part_nodes:
            if n not in S:
                continue
            v = principal_max(S[n]) if brittle else mises(S[n])
            evals_all.append(v)
            excluded = near_bc(nodes[n])
            if not excluded:
                evals.append(v)
            if max_info is None or v > max_info[0]:
                max_info = (v, n, excluded)
        p99 = percentile(evals, 99)
        allow = pc.get('allowable')
        sf_req = cfg.get('safety_factor_required') or 1.0
        ur = p99 / (allow / sf_req) if allow else None

        umax = None
        if U:
            for n in part_nodes:
                if n in U:
                    m = math.sqrt(sum(c*c for c in U[n][:3]))
                    if umax is None or m > umax[0]:
                        umax = (m, n)

        parts_out[p] = {
            'display_name': pc.get('display_name', p), 'material': mat_name,
            'criterion': '最大主應力' if brittle else 'von Mises',
            'volume': vol, 'mass': mass, 'cg': cg, 'I_cg': Icg, 'strain_energy': se,
            'stress_p99': p99, 'stress_max': max_info[0] if max_info else None,
            'stress_max_node': max_info[1] if max_info else None,
            'stress_max_in_excluded_zone': max_info[2] if max_info else None,
            'allowable': allow, 'UR': ur,
            'umax': umax[0] if umax else None, 'umax_node': umax[1] if umax else None,
            'moving_axis': pc.get('moving_axis'),
        }

    # ---- 佔比與排名 ----
    tot_se = sum(v['strain_energy'] for v in parts_out.values())
    tot_m = sum(v['mass'] for v in parts_out.values())
    max_ur = max([v['UR'] for v in parts_out.values() if v['UR'] is not None] or [0])
    ws, wk = cfg['weights']['strength'], cfg['weights']['stiffness']
    for v in parts_out.values():
        v['SE_share'] = 100*v['strain_energy']/tot_se if tot_se else 0
        v['mass_share'] = 100*v['mass']/tot_m if tot_m else 0
        v['eff'] = v['SE_share']/v['mass_share'] if v['mass_share'] else None
        urn = (v['UR']/max_ur) if (v['UR'] is not None and max_ur) else 0
        v['score'] = ws*urn + wk*v['SE_share']/100
    tot_score = sum(v['score'] for v in parts_out.values())
    for v in parts_out.values():
        v['risk_share'] = 100*v['score']/tot_score if tot_score else 0
    ranking = sorted(parts_out, key=lambda k: -parts_out[k]['risk_share'])

    # ---- 零件內熱區（依元素應變能密度） ----
    hotspots = {}
    top_pct = cfg.get('hotspot_top_percent', 5)
    cdist = cfg.get('hotspot_cluster_distance')
    for p in parts_out:
        recs = sorted([r for r in elem_records if r['part'] == p], key=lambda r: -r['sed'])
        k = max(1, int(len(recs)*top_pct/100))
        top = recs[:k]
        clusters = []
        for r in top:
            placed = False
            if cdist:
                for c in clusters:
                    d = math.dist(r['centroid'], c['seed'])
                    if d <= cdist:
                        c['members'].append(r); placed = True; break
            if not placed:
                clusters.append({'seed': r['centroid'], 'members': [r]})
        out = []
        for c in clusters:
            se_sum = sum(m['se'] for m in c['members'])
            vsum = sum(m['vol'] for m in c['members'])
            cen = tuple(sum(m['centroid'][i]*m['vol'] for m in c['members'])/vsum for i in range(3))
            out.append({'centroid': cen, 'se': se_sum, 'n_elem': len(c['members']),
                        'share_of_part': 100*se_sum/parts_out[p]['strain_energy'] if parts_out[p]['strain_energy'] else 0,
                        'region': region_name(cfg, p, cen), 'near_bc': near_bc(cen)})
        out.sort(key=lambda h: -h['se'])
        hotspots[p] = out

    # ---- 建議類型 ----
    for i, p in enumerate(ranking):
        v = parts_out[p]
        tags = []
        if v['UR'] is not None and v['UR'] >= 1:
            tags.append('必須處理：強度不符要求')
        if i == 0:
            tags.append('第一優先優化零件')
        elif i == 1:
            tags.append('第二優先優化零件')
        if v['eff'] is not None and len(parts_out) > 1:
            if v['eff'] > 1:
                tags.append('剛性貢獻高於其質量比例 → 補強候選')
            elif v['eff'] < 1 and (v['UR'] is None or v['UR'] < 0.5) and v['moving_axis']:
                tags.append('移動件且剛性貢獻低於質量比例 → 減重候選')
        if v['UR'] is not None and v['SE_share'] < 100/len(parts_out) and v['UR'] >= 0.8:
            tags.append('應力高但剛性貢獻低 → 可能為局部應力集中，建議局部修改')
        v['advice'] = tags

    return {'model': model, 'units': units, 'parts': parts_out, 'ranking': ranking,
            'hotspots': hotspots, 'warnings': warnings, 'total_se': tot_se, 'total_mass': tot_m,
            'work_check': work_check(model, res)}

def region_name(cfg, part, p):
    for r in cfg.get('regions', []):
        if r.get('part') != part:
            continue
        try:
            if r['xmin'] <= p[0] <= r['xmax'] and r['ymin'] <= p[1] <= r['ymax'] and r['zmin'] <= p[2] <= r['zmax']:
                return r.get('name') or r.get('_例')
        except (KeyError, TypeError):
            pass
    return ''

def work_check(model, res):
    """驗證用：外力功 ½ΣF·u。外力取節點力（FORC），只計非邊界節點。"""
    F, U = res.get('FORC'), res.get('DISP')
    if not F or not U:
        return None
    bc = set()
    for s in model['boundary_sets']:
        bc.update(model['nsets'].get(s, []))
    w = 0.0
    for n, f in F.items():
        if n in bc or n not in U:
            continue
        w += sum(f[i]*U[n][i] for i in range(3))
    return 0.5*abs(w)

# ----------------------------------------------------------------------------
# 6. 輸出
# ----------------------------------------------------------------------------

def fmt(x, nd=4):
    if x is None:
        return '—'
    if isinstance(x, float):
        return f'{x:.{nd}g}'
    return str(x)

def write_outputs(r, outdir, cfg):
    os.makedirs(outdir, exist_ok=True)
    u = r['units']
    kg = u.get('mass_to_kg')
    P = r['parts']
    # CSV
    with open(os.path.join(outdir, 'parts.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['排名', '零件', '顯示名稱', '材料', '判斷準則', f'P99應力({u["stress"]})', f'最大應力({u["stress"]})',
                    '容許值', '利用率UR', f'應變能({u["energy"]})', '剛性貢獻%', '質量(kg)', '質量%', '效率比',
                    '風險佔比%', '建議'])
        for i, p in enumerate(r['ranking'], 1):
            v = P[p]
            w.writerow([i, p, v['display_name'], v['material'], v['criterion'], fmt(v['stress_p99']), fmt(v['stress_max']),
                        fmt(v['allowable']), fmt(v['UR']), fmt(v['strain_energy']), fmt(v['SE_share']),
                        fmt(v['mass']*kg if kg else v['mass']), fmt(v['mass_share']), fmt(v['eff']),
                        fmt(v['risk_share']), '；'.join(v['advice'])])
    with open(os.path.join(outdir, 'hotspots.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['零件', '熱區排名', 'X', 'Y', 'Z', '區域名稱', '元素數', f'應變能({u["energy"]})', '佔該零件%', '位於邊界條件排除區'])
        for p, hs in r['hotspots'].items():
            for i, h in enumerate(hs[:10], 1):
                w.writerow([p, i, fmt(h['centroid'][0]), fmt(h['centroid'][1]), fmt(h['centroid'][2]), h['region'],
                            h['n_elem'], fmt(h['se']), fmt(h['share_of_part']), '是' if h['near_bc'] else ''])

    # Markdown 報告
    L = []
    L.append('# 結構評估報告\n')
    L.append(f'- 單位系統：{r["model"]["unit_system"]}（應力 {u["stress"]}，能量 {u["energy"]}）')
    L.append(f'- 權重：強度 {cfg["weights"]["strength"]}，剛性 {cfg["weights"]["stiffness"]}')
    L.append(f'- 熱點排除半徑（邊界條件周圍）：{fmt(cfg.get("exclude_bc_radius"))}')
    if r['warnings']:
        L.append('\n## ⚠ 需確認事項')
        for wn in r['warnings']:
            L.append(f'- {wn}')
    L.append('\n## 零件排名')
    L.append('| 排名 | 零件 | 判斷準則 | P99 應力 | 利用率 | 剛性貢獻% | 質量 kg | 質量% | 效率比 | 風險佔比% | 建議 |')
    L.append('|---|---|---|---|---|---|---|---|---|---|---|')
    for i, p in enumerate(r['ranking'], 1):
        v = P[p]
        L.append(f'| {i} | {v["display_name"]} | {v["criterion"]} | {fmt(v["stress_p99"])} | {fmt(v["UR"])} | '
                 f'{fmt(v["SE_share"])} | {fmt(v["mass"]*kg if kg else v["mass"])} | {fmt(v["mass_share"])} | '
                 f'{fmt(v["eff"])} | {fmt(v["risk_share"])} | {"；".join(v["advice"])} |')
    L.append('\n## 質量與慣性（對重心）')
    for p in r['ranking']:
        v = P[p]
        cg = v['cg']
        L.append(f'- **{v["display_name"]}**：質量 {fmt(v["mass"]*kg if kg else v["mass"])} kg，'
                 f'重心 ({fmt(cg[0])}, {fmt(cg[1])}, {fmt(cg[2])}) {u["length"]}，'
                 + '，'.join(f'{k} {fmt(val)}' for k, val in v['I_cg'].items()) + f'（{"t·mm²" if kg == 1000 else "模型單位"}）')
    L.append('\n## 各零件熱區（依應變能，前 2 名）')
    for p in r['ranking']:
        for i, h in enumerate(r['hotspots'][p][:2], 1):
            c = h['centroid']
            L.append(f'- {P[p]["display_name"]} 第 {i} 熱區：({fmt(c[0])}, {fmt(c[1])}, {fmt(c[2])}) {h["region"]}'
                     f'，佔該零件應變能 {fmt(h["share_of_part"])}%' + ('（位於邊界條件排除區）' if h['near_bc'] else ''))
    L.append('\n## 計算方法')
    L.append('- 強度：結構鋼用 von Mises，灰鑄鐵用最大主應力；取零件節點值第 99 百分位數（排除邊界條件周圍）。利用率 = P99 ÷（容許值 ÷ 安全係數）。')
    L.append('- 剛性貢獻：各零件應變能 ÷ 總應變能。應變能由節點應力以形函數內插、在積分點計算 ½σ:ε 後積分。線性靜力且以外力加載時，此佔比等於對整機柔度的貢獻佔比。')
    L.append('- 質量、重心、慣性：由元素幾何與材料密度積分。')
    L.append('- 效率比 = 剛性貢獻% ÷ 質量%（本工具定義的排序指標，非業界標準）。')
    L.append('- 風險佔比 = 各零件（強度權重 × 正規化利用率 + 剛性權重 × 剛性貢獻）÷ 總和，為相對優先順序，不是失效機率。')
    wc = r['work_check']
    if wc:
        L.append(f'\n## 驗證\n- 外力功 ½ΣF·u = {fmt(wc, 6)}，計算的總應變能 = {fmt(r["total_se"], 6)}（兩者應接近）')
    with open(os.path.join(outdir, 'report.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')

    # 給 AI 的摘要
    A = ['以下是結構分析的計算結果，請依此撰寫給設計者的優化建議（繁體中文，只依據數據，不要推測數據以外的內容）：']
    for i, p in enumerate(r['ranking'], 1):
        v = P[p]
        hs = r['hotspots'][p][:2]
        A.append(f'{i}. {v["display_name"]}：風險佔比 {fmt(v["risk_share"])}%，剛性貢獻 {fmt(v["SE_share"])}%，'
                 f'質量 {fmt(v["mass_share"])}%，利用率 {fmt(v["UR"])}，建議類型：{"；".join(v["advice"])}；'
                 + '；'.join(f'熱區{j+1} 座標({fmt(h["centroid"][0])},{fmt(h["centroid"][1])},{fmt(h["centroid"][2])}) {h["region"]}'
                            for j, h in enumerate(hs)))
    with open(os.path.join(outdir, 'ai_summary.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(A) + '\n')

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    inp, frd = sys.argv[1], sys.argv[2]
    cfg_path = sys.argv[3] if len(sys.argv) > 3 else 'fea_config.json'
    if not os.path.exists(cfg_path):
        make_config_template(read_inp(inp), cfg_path)
        print(f'已產生設定檔範本：{cfg_path}，請填好後再執行。')
        sys.exit(0)
    with open(cfg_path, encoding='utf-8') as f:
        cfg = json.load(f)
    r = evaluate(inp, frd, cfg)
    outdir = os.path.splitext(frd)[0] + '_評估結果'
    write_outputs(r, outdir, cfg)
    print(f'完成，結果在：{outdir}')
    for wn in r['warnings']:
        print('⚠', wn)

if __name__ == '__main__':
    main()
