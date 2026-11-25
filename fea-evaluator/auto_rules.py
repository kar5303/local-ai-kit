# -*- coding: utf-8 -*-
"""
組件自動判斷規則（只用幾何與網格，不依賴零件命名；材料除外，材料依名稱代號）
輸入：PrePoMax read_topology 的面資料（每面 id、type、area、centroid、normal）＋網格（.inp）
輸出：每項判斷的結果與「依據」，判斷不成立時回傳 ok=False 與原因，不猜測。

判斷項目
1. 材料：零件名稱含 rules/materials.json 的代號
2. 質量、重心：由網格積分
3. 零件接合（Tie）：兩零件的平面，法向相反、共平面、且彼此重疊
4. 重力方向與支撐面：見 detect_gravity()
5. 主軸端與工作台面（切削力作用點）：見 detect_tool_and_table()
"""
import math, json, os, re
import fea_eval

# ---------------- 向量 ----------------
def sub(a, b): return [a[0]-b[0], a[1]-b[1], a[2]-b[2]]
def add(a, b): return [a[0]+b[0], a[1]+b[1], a[2]+b[2]]
def mul(a, s): return [a[0]*s, a[1]*s, a[2]*s]
def dot(a, b): return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def cross(a, b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
def norm(a): return math.sqrt(dot(a, a))
def unit(a):
    n = norm(a); return [a[0]/n, a[1]/n, a[2]/n]

PAR = 0.999          # 平行判定 cos 值（約 2.6°）

def plane_basis(d):
    t = [1, 0, 0] if abs(d[0]) < 0.9 else [0, 1, 0]
    e1 = unit(cross(d, t)); e2 = cross(d, e1)
    return e1, e2

def convex_hull(pts):
    pts = sorted(set((round(p[0], 6), round(p[1], 6)) for p in pts))
    if len(pts) < 3: return pts
    def cr(o, a, b): return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], p) <= 0: lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cr(up[-2], up[-1], p) <= 0: up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]

def hull_margin(hull, q):
    """點 q 到凸包邊界的帶號距離：>0 在內部，<0 在外部。"""
    if len(hull) < 3: return -1e30
    m = 1e30
    for i in range(len(hull)):
        a, b = hull[i], hull[(i+1) % len(hull)]
        ex, ey = b[0]-a[0], b[1]-a[1]; L = math.hypot(ex, ey)
        if L == 0: continue
        m = min(m, (ex*(q[1]-a[1]) - ey*(q[0]-a[0])) / L)   # 逆時針凸包，內側為正
    return m

# ---------------- 材料 ----------------
def load_materials(path):
    return json.load(open(path, encoding='utf-8'))['materials']

def assign_materials(part_names, mats):
    out, missing = {}, []
    for p in part_names:
        hit = [c for c in mats if re.search(re.escape(c), p, re.I)]
        hit.sort(key=len, reverse=True)
        if hit: out[p] = hit[0]
        else: missing.append(p)
    return out, missing

# ---------------- 網格：質量、重心、節點 ----------------
_TET_OF_HEX = [(0, 1, 3, 4), (1, 2, 3, 6), (1, 4, 5, 6), (3, 4, 6, 7), (1, 3, 4, 6)]

def mesh_parts(model):
    """回傳 {part: {'nodes': set, 'vol', 'cg'}}（零件 = .inp 的 *Element ELSET）"""
    nodes = model['nodes']; out = {}
    for p in model['part_sets']:
        vol = 0.0; m1 = [0.0, 0.0, 0.0]; ns = set()
        for eid in model['elsets'][p]:
            conn = model['elements'][eid]; et = model['elem_type'][eid].upper(); ns.update(conn)
            tets = [(0, 1, 2, 3)] if et.startswith('C3D4') or et.startswith('C3D10') else \
                   _TET_OF_HEX if et.startswith('C3D8') or et.startswith('C3D20') else \
                   [(0, 1, 2, 3), (1, 2, 3, 4), (2, 3, 4, 5)] if et.startswith('C3D6') or et.startswith('C3D15') else []
            for t in tets:
                a, b, c, d = (nodes[conn[k]] for k in t)
                v = abs(dot(sub(b, a), cross(sub(c, a), sub(d, a)))) / 6.0
                cen = [(a[k]+b[k]+c[k]+d[k])/4 for k in range(3)]
                vol += v; m1 = add(m1, mul(cen, v))
        out[p] = {'nodes': ns, 'vol': vol, 'cg': mul(m1, 1/vol) if vol else None}
    return out

def nodes_on_plane(model, node_ids, n, c, tol):
    h = dot(n, c)
    return [i for i in node_ids if abs(dot(model['nodes'][i], n) - h) <= tol]

# ---------------- 面資料 ----------------
def faces_by_part(topo):
    return {pt['name']: {'id': pt['id'], 'faces': pt.get('faces', []), 'bbox': pt.get('bounding_box')}
            for pt in topo.get('femodel', [])}

def planar(f): return f.get('type') == 'Plane' and f.get('normal')

# ---------------- 3. Tie ----------------
def detect_ties(model, fp, mp, tol):
    hull_cache = {}
    def plane_hull(part, n, c):
        key = (part, tuple(round(x, 4) for x in n), round(dot(n, c), 3))
        if key not in hull_cache:
            e1, e2 = plane_basis(n)
            ids = nodes_on_plane(model, mp[part]['nodes'], n, c, tol)
            hull_cache[key] = (convex_hull([(dot(model['nodes'][i], e1), dot(model['nodes'][i], e2)) for i in ids]), e1, e2)
        return hull_cache[key]
    pairs = []
    names = list(fp)
    for i, a in enumerate(names):
        for b in names[i+1:]:
            for fa in filter(planar, fp[a]['faces']):
                for fb in filter(planar, fp[b]['faces']):
                    if dot(fa['normal'], fb['normal']) > -PAR: continue
                    if abs(dot(fa['normal'], sub(fb['centroid'], fa['centroid']))) > tol: continue
                    ha, e1, e2 = plane_hull(a, fa['normal'], fa['centroid'])
                    hb, g1, g2 = plane_hull(b, fb['normal'], fb['centroid'])
                    qa = (dot(fb['centroid'], e1), dot(fb['centroid'], e2))
                    qb = (dot(fa['centroid'], g1), dot(fa['centroid'], g2))
                    if hull_margin(ha, qa) <= 0 and hull_margin(hb, qb) <= 0: continue
                    m, s = ((a, fa), (b, fb)) if fa['area'] >= fb['area'] else ((b, fb), (a, fa))
                    pairs.append({'master_part': m[0], 'master_face': m[1]['id'], 'slave_part': s[0], 'slave_face': s[1]['id'],
                                  'normal': fa['normal'], 'area': min(fa['area'], fb['area']), 'centroid': s[1]['centroid']})
    return pairs

def connectivity(parts, ties, roots):
    adj = {p: set() for p in parts}
    for t in ties: adj[t['master_part']].add(t['slave_part']); adj[t['slave_part']].add(t['master_part'])
    seen, st = set(roots), list(roots)
    while st:
        p = st.pop()
        for q in adj[p]:
            if q not in seen: seen.add(q); st.append(q)
    return [p for p in parts if p not in seen]

# ---------------- 4. 重力方向與支撐面 ----------------
def detect_gravity(model, fp, mp, masses, ties, tol):
    """
    候選方向 = 所有平面的法向（合併平行者）。對每個候選「向下」方向 d：
      支撐面 = 沿 d 最遠處（最低處）的平面，可分屬多個零件
      C1 支撐面存在，且支撐節點至少圍成一個面（不是只有一條線）
      C2 支撐零件包含最重的零件（機台站在底座上；零件同重時任一即可）
      C3 組件重心投影落在支撐節點凸包內（站得住），記錄邊界餘裕 m
      C4 零件接合面中，法向與 d 平行的面積比例（承重接合面垂直於重力）
      S  穩定指數 = 餘裕 m ÷ 重心高度 h（傾倒前可承受的水平加速度比，越大越穩）
    選擇：通過 C1～C3 者中，先看 C4：與最大 C4 相差 0.15 以內的方向再比 S，取 S 最大；
          前兩名 S 相差不到 10% 視為無法區分，停止。
    """
    dirs = []
    for p in fp:
        for f in filter(planar, fp[p]['faces']):
            d = mul(unit(f['normal']), 1.0)
            if not any(dot(d, e) > PAR for e in dirs): dirs.append(d)
    M = sum(masses.values())
    cg = mul([sum(masses[p]*mp[p]['cg'][k] for p in masses) for k in range(3)], 1/M)
    mmax = max(masses.values())
    heaviest = [p for p in masses if masses[p] >= mmax * (1 - 1e-6)]
    tie_area = sum(t['area'] for t in ties) or 1.0
    allx = [model['nodes'][i] for p in mp for i in mp[p]['nodes']]
    rows = []
    for d in dirs:
        ext = max(dot(x, d) for x in allx)
        sup = [(p, f) for p in fp for f in filter(planar, fp[p]['faces'])
               if dot(f['normal'], d) > PAR and dot(f['centroid'], d) >= ext - tol]
        sparts = sorted({p for p, _ in sup})
        r = {'down': [round(x, 4) for x in d], 'support_parts': sparts, 'support_faces': [(p, f['id']) for p, f in sup],
             'support_area': sum(f['area'] for _, f in sup)}
        e1, e2 = plane_basis(d)
        ids = [i for p in sparts for i in mp[p]['nodes'] if dot(model['nodes'][i], d) >= ext - tol]
        hull = convex_hull([(dot(model['nodes'][i], e1), dot(model['nodes'][i], e2)) for i in ids])
        r['C1_support'] = bool(sup) and len(hull) >= 3
        r['C2_heaviest'] = any(h in sparts for h in heaviest)
        r['C3_margin'] = hull_margin(hull, (dot(cg, e1), dot(cg, e2))) if r['C1_support'] else None
        r['C3_stable'] = r['C3_margin'] is not None and r['C3_margin'] > 0
        r['cg_height'] = ext - dot(cg, d)
        r['stability'] = (r['C3_margin'] / r['cg_height']) if r['C3_stable'] and r['cg_height'] > 0 else None
        r['C4_bearing_ratio'] = sum(t['area'] for t in ties if abs(dot(t['normal'], d)) > PAR) / tie_area
        r['pass'] = r['C1_support'] and r['C2_heaviest'] and r['C3_stable']
        r['_hull'] = hull; r['_e'] = (e1, e2); r['_ext'] = ext; r['_ids'] = ids
        rows.append(r)
    ok = [r for r in rows if r['pass']]
    res = {'cg': cg, 'heaviest': heaviest[0], 'candidates': rows}
    if not ok:
        res.update(ok=False, reason='沒有任何方向同時滿足 C1～C3（有支撐面、支撐零件含最重零件、重心落在支撐範圍內）')
        return res
    c4 = max(r['C4_bearing_ratio'] for r in ok)
    near = sorted([r for r in ok if r['C4_bearing_ratio'] >= c4 - 0.15], key=lambda r: -r['stability'])
    res['shortlist'] = near
    if len(near) > 1 and near[1]['stability'] >= 0.9 * near[0]['stability']:
        res.update(ok=False, reason=f"通過的方向中，前兩名穩定指數相差不到 10%（{near[0]['stability']:.3f} vs {near[1]['stability']:.3f}），無法區分")
        return res
    res.update(ok=True, chosen=near[0], rule=('C4' if len(near) == 1 else 'C4+S'))
    return res

def soft_support_points(model, chosen):
    """偏柔支撐用：支撐面上距離最遠的兩個節點 A、B（只限制水平剛體運動）"""
    ids = chosen['_ids']; e1, e2 = chosen['_e']
    P = {i: (dot(model['nodes'][i], e1), dot(model['nodes'][i], e2)) for i in ids}
    a = min(ids, key=lambda i: (P[i][0] + P[i][1]))
    b = max(ids, key=lambda i: (P[i][0]-P[a][0])**2 + (P[i][1]-P[a][1])**2)
    return a, b

# ---------------- 5. 主軸端與工作台面 ----------------
def detect_tool_and_table(model, fp, mp, ties, tol):
    """
    主軸候選：圓柱面面積佔該零件表面積 > 50% 的零件。
    軸向：該零件平面的法向（端面），須成對 ±（兩端面平行）。
    主軸端：軸向端面中沒有參與 Tie 的那一面（自由端）。
    工作台面：由主軸端中心沿端面法向射出，第一個碰到的「他零件平面」，該面法向與射線相反、
              且射線落點在該零件該平面的節點凸包內；距離最短者。
    多個候選時取射線距離最短者，並列出全部候選。
    """
    tied = {(t['master_part'], t['master_face']) for t in ties} | {(t['slave_part'], t['slave_face']) for t in ties}
    cands = []
    for p in fp:
        fs = fp[p]['faces']; A = sum(f['area'] for f in fs) or 1
        cyl = sum(f['area'] for f in fs if f.get('type') == 'Cylinder')
        if cyl / A <= 0.5: continue
        ends = [f for f in fs if planar(f)]
        for f in ends:
            if (p, f['id']) in tied: continue
            if not any(dot(f['normal'], g['normal']) < -PAR for g in ends if g is not f): continue
            n = unit(f['normal']); c = f['centroid']; best = None
            for q in fp:
                if q == p: continue
                for g in filter(planar, fp[q]['faces']):
                    if dot(g['normal'], n) > -PAR: continue
                    t = dot(sub(g['centroid'], c), n)
                    if t <= tol: continue
                    hit = add(c, mul(n, t))
                    e1, e2 = plane_basis(g['normal'])
                    ids = nodes_on_plane(model, mp[q]['nodes'], g['normal'], g['centroid'], tol)
                    hull = convex_hull([(dot(model['nodes'][i], e1), dot(model['nodes'][i], e2)) for i in ids])
                    if hull_margin(hull, (dot(hit, e1), dot(hit, e2))) <= 0: continue
                    if best is None or t < best['gap']:
                        best = {'table_part': q, 'table_face': g['id'], 'gap': t, 'hit': hit}
            cands.append({'spindle_part': p, 'nose_face': f['id'], 'nose_center': c, 'axis_out': n,
                          'nose_area': f['area'], 'cyl_ratio': cyl / A, 'table': best})
    good = [x for x in cands if x['table']]
    good.sort(key=lambda x: x['table']['gap'])
    if not good:
        return {'ok': False, 'candidates': cands,
                'reason': '找不到「圓柱為主、有自由端面、且端面正對另一零件平面」的零件，無法判斷切削力位置'}
    return {'ok': True, 'chosen': good[0], 'candidates': cands}

def face_nodes_near(model, part_nodes, n, c, center, radius, tol):
    ids = nodes_on_plane(model, part_nodes, n, c, tol)
    return [i for i in ids if norm(sub(sub(model['nodes'][i], center), mul(n, dot(sub(model['nodes'][i], center), n)))) <= radius]

# ---------------- 5b. 找不到主軸時：離支撐最遠的自由面 ----------------
def detect_free_end(model, fp, mp, ties, support_faces, tol, down=None):
    """
    規則：所有平面中，排除參與 Tie 的面與支撐面，取「離支撐面最高」的面（沿重力反方向的高度最大）作為受力面；
    高度相同（差 < 容差）時取離支撐面形心最遠者。結構固定在支撐面上時，最高處的自由面就是懸伸最長、最容易變形的位置。
    用於單一零件，或組件中找不到主軸與工作台時。
    """
    tied = {(t['master_part'], t['master_face']) for t in ties} | {(t['slave_part'], t['slave_face']) for t in ties}
    sup = set(support_faces)
    sf = [f for p in fp for f in fp[p]['faces'] if (p, f['id']) in sup]
    A = sum(f['area'] for f in sf) or 1
    sc = [sum(f['centroid'][k]*f['area'] for f in sf)/A for k in range(3)]
    up = mul(down, -1.0) if down else None
    best = None
    for p in fp:
        for f in filter(planar, fp[p]['faces']):
            if (p, f['id']) in tied or (p, f['id']) in sup: continue
            dist = norm(sub(f['centroid'], sc))
            h = dot(sub(f['centroid'], sc), up) if up else 0.0
            key = (round(h / max(tol, 1e-9)), dist)
            if best is None or key > best['_key']:
                best = {'part': p, 'face': f['id'], 'distance': dist, 'height': h, 'centroid': f['centroid'], 'normal': f['normal'], 'area': f['area'], '_key': key}
    if best is None:
        return {'ok': False, 'reason': '沒有可用的自由平面'}
    best.pop('_key')
    # 同一高度、同一法向、未接合的所有平面一起受力（例如一個頂面被切成數個零件）
    group = []
    for p in fp:
        for f in filter(planar, fp[p]['faces']):
            if (p, f['id']) in tied or (p, f['id']) in sup: continue
            if dot(f['normal'], best['normal']) < PAR: continue
            if abs(dot(sub(f['centroid'], best['centroid']), best['normal'])) > tol: continue
            if up and abs(dot(sub(f['centroid'], sc), up) - best['height']) > tol: continue
            group.append({'part': p, 'face': f['id'], 'area': f['area'], 'centroid': f['centroid'], 'normal': f['normal']})
    best['group'] = group or [{'part': best['part'], 'face': best['face'], 'area': best['area'], 'centroid': best['centroid'], 'normal': best['normal']}]
    return {'ok': True, 'chosen': best, 'support_centroid': sc}

def region_blocks(elements, parts, n_div=4):
    """
    把每個零件依其元素形心包圍盒切成格子（最長邊切 n_div 段，其餘邊依同樣格距），
    回傳 {(part, i, j, k): {'bounds': [xmin,xmax,ymin,ymax,zmin,zmax], 'eids': set}}，供跨工況比較同一位置。
    """
    out = {}
    for p in parts:
        es = [e for e in elements if e['part'] == p]
        if not es: continue
        lo = [min(e['centroid'][k] for e in es) for k in range(3)]
        hi = [max(e['centroid'][k] for e in es) for k in range(3)]
        h = max(hi[k]-lo[k] for k in range(3)) / n_div or 1.0
        nd = [max(1, math.ceil((hi[k]-lo[k]) / h - 1e-9)) for k in range(3)]
        for e in es:
            idx = tuple(min(nd[k]-1, int((e['centroid'][k]-lo[k]) / h)) for k in range(3))
            key = (p,) + idx
            b = out.setdefault(key, {'bounds': [1e30, -1e30, 1e30, -1e30, 1e30, -1e30], 'eids': set()})
            b['eids'].add(e['eid'])
            for k in range(3):
                b['bounds'][2*k] = min(b['bounds'][2*k], e['centroid'][k]); b['bounds'][2*k+1] = max(b['bounds'][2*k+1], e['centroid'][k])
    return out
