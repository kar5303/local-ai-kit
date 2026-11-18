# -*- coding: utf-8 -*-
"""
從 STEP 組件讀出每個實體零件的名稱（PRODUCT 名稱）與在組件中的位置。
PrePoMax 3.0.1 匯入 STEP 時零件一律命名為 Solid_part-N，不保留 CAD 名稱，
所以由本程式讀名稱，再以「頂點包圍盒落在 PrePoMax 零件包圍盒內」對應。
只用標準函式庫。
"""
import re, math

def _parse(path):
    s = open(path, encoding='utf-8', errors='replace').read()
    i = s.find('DATA;'); j = s.rfind('ENDSEC;')
    body = s[i+5:j]
    ents = {}
    for m in re.finditer(r'#(\d+)\s*=\s*(.*?);\s*(?=#\d+\s*=|\Z)', body, re.S):
        ents[int(m.group(1))] = ' '.join(m.group(2).split())
    return ents

def _type(e):
    e = e.strip()
    if e.startswith('('): return 'COMPLEX'
    return e.split('(', 1)[0].strip()

def _args(e):
    """頂層參數切分（不處理複合實體）"""
    a = e[e.index('(')+1:e.rindex(')')]
    out, depth, cur, q = [], 0, '', False
    for ch in a:
        if ch == "'" : q = not q
        if not q and ch == '(': depth += 1
        if not q and ch == ')': depth -= 1
        if not q and depth == 0 and ch == ',':
            out.append(cur.strip()); cur = ''
        else: cur += ch
    out.append(cur.strip())
    return out

def _ref(x): return int(x[1:]) if x.startswith('#') else None
def _refs(x): return [int(r) for r in re.findall(r'#(\d+)', x)]
def _str(x): return x[1:-1] if x.startswith("'") and x.endswith("'") else x

def _floats(x): return [float(v) for v in re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', x)]

def _axis(ents, aid):
    a = _args(ents[aid])
    p = _floats(_args(ents[_ref(a[1])])[1])
    z = _floats(_args(ents[_ref(a[2])])[1]) if a[2] != '$' else [0, 0, 1]
    x = _floats(_args(ents[_ref(a[3])])[1]) if a[3] != '$' else ([1, 0, 0] if abs(z[0]) < 0.9 else [0, 1, 0])
    n = math.sqrt(sum(v*v for v in z)); z = [v/n for v in z]
    d = sum(x[k]*z[k] for k in range(3)); x = [x[k]-d*z[k] for k in range(3)]
    n = math.sqrt(sum(v*v for v in x)); x = [v/n for v in x]
    y = [z[1]*x[2]-z[2]*x[1], z[2]*x[0]-z[0]*x[2], z[0]*x[1]-z[1]*x[0]]
    R = [[x[i], y[i], z[i]] for i in range(3)]
    return R, p

def _compose(A, B):  # A∘B
    (Ra, pa), (Rb, pb) = A, B
    R = [[sum(Ra[i][k]*Rb[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    p = [sum(Ra[i][k]*pb[k] for k in range(3)) + pa[i] for i in range(3)]
    return R, p

def _inv(A):
    R, p = A; Rt = [[R[j][i] for j in range(3)] for i in range(3)]
    return Rt, [-sum(Rt[i][k]*p[k] for k in range(3)) for i in range(3)]

def _apply(A, x):
    R, p = A; return [sum(R[i][k]*x[k] for k in range(3)) + p[i] for i in range(3)]

I = ([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0])

def read_step_parts(path):
    ents = _parse(path)
    typ = {k: _type(v) for k, v in ents.items()}
    # PRODUCT_DEFINITION → 名稱
    pd_name = {}
    for k, t in typ.items():
        if t == 'PRODUCT_DEFINITION':
            pdf = _ref(_args(ents[k])[2]); prod = _ref(_args(ents[pdf])[2])
            pd_name[k] = _str(_args(ents[prod])[0])
    # PRODUCT_DEFINITION → 形狀表示
    pd_reps = {}
    for k, t in typ.items():
        if t == 'SHAPE_DEFINITION_REPRESENTATION':
            a = _args(ents[k]); pds = _ref(a[0]); rep = _ref(a[1])
            tgt = _ref(_args(ents[pds])[2])
            if typ.get(tgt) == 'PRODUCT_DEFINITION':
                pd_reps.setdefault(tgt, []).append(rep)
    # 無轉換的 SHAPE_REPRESENTATION_RELATIONSHIP（SR ↔ ABSR）
    rel = {}
    for k, t in typ.items():
        if t == 'SHAPE_REPRESENTATION_RELATIONSHIP':
            a = _args(ents[k]); r1, r2 = _ref(a[2]), _ref(a[3])
            rel.setdefault(r1, set()).add(r2); rel.setdefault(r2, set()).add(r1)
    def geom_reps(pd):
        out, st = set(), list(pd_reps.get(pd, []))
        while st:
            r = st.pop()
            if r in out: continue
            out.add(r); st += list(rel.get(r, []))
        return out
    def solids(pd):
        res = []
        for r in geom_reps(pd):
            if typ.get(r) is None or '(' not in ents[r]: continue
            items = _refs(_args(ents[r])[1]) if typ[r] != 'COMPLEX' else []
            res += [i for i in items if typ.get(i) in ('MANIFOLD_SOLID_BREP', 'BREP_WITH_VOIDS', 'FACETED_BREP')]
        return res
    # 組件實例與轉換
    children = {}
    for k, t in typ.items():
        if t == 'NEXT_ASSEMBLY_USAGE_OCCURRENCE':
            a = _args(ents[k]); children.setdefault(_ref(a[3]), []).append((k, _ref(a[4])))
    nauo_tf = {}
    for k, t in typ.items():
        if t == 'CONTEXT_DEPENDENT_SHAPE_REPRESENTATION':
            a = _args(ents[k]); rr = _ref(a[0]); pds = _ref(a[1])
            nauo = _ref(_args(ents[pds])[2])
            idt = re.search(r'REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION\(\s*#(\d+)', ents[rr])
            if idt:
                ia = _args(ents[int(idt.group(1))])
                A1, A2 = _axis(ents, _ref(ia[2])), _axis(ents, _ref(ia[3]))
                nauo_tf[nauo] = _compose(A2, _inv(A1))
    def verts(sid):
        pts, seen, st = [], set(), [sid]
        while st:
            e = st.pop()
            if e in seen: continue
            seen.add(e); t = typ.get(e)
            if t == 'VERTEX_POINT':
                pts.append(_floats(_args(ents[_ref(_args(ents[e])[1])])[1])); continue
            if t in ('MANIFOLD_SOLID_BREP', 'BREP_WITH_VOIDS', 'CLOSED_SHELL', 'OPEN_SHELL', 'ADVANCED_FACE', 'FACE_BOUND',
                     'FACE_OUTER_BOUND', 'EDGE_LOOP', 'ORIENTED_EDGE', 'EDGE_CURVE', 'ORIENTED_CLOSED_SHELL', 'VERTEX_LOOP'):
                st += _refs(ents[e])
        return pts
    child_pds = {c for lst in children.values() for _, c in lst}
    roots = [pd for pd in pd_name if pd not in child_pds]
    parts = []
    def walk(pd, T, path):
        for sid in solids(pd):
            P = [_apply(T, v) for v in verts(sid)]
            bb = [min(p[0] for p in P), max(p[0] for p in P), min(p[1] for p in P), max(p[1] for p in P),
                  min(p[2] for p in P), max(p[2] for p in P)] if P else None
            parts.append({'name': pd_name[pd], 'path': path + [pd_name[pd]], 'vertex_bbox': bb})
        for nauo, c in sorted(children.get(pd, []), key=lambda x: x[0]):
            walk(c, _compose(T, nauo_tf.get(nauo, I)), path + [pd_name[pd]])
    for r in roots: walk(r, I, [])
    return parts

def _inside(inner, outer, tol):
    return all(inner[2*k] >= outer[2*k] - tol and inner[2*k+1] <= outer[2*k+1] + tol for k in range(3))

def match_parts(step_parts, pmx_parts, tol=None):
    """
    pmx_parts：[(name, bbox[xmin,xmax,ymin,ymax,zmin,zmax])]（PrePoMax 匯入順序）
    先依順序對應並檢查；順序檢查不通過時，改以「包含關係唯一」對應。回傳 ({pmx_name: step_name}, 說明)
    """
    if len(step_parts) != len(pmx_parts):
        return None, f'STEP 實體數 {len(step_parts)} 與 PrePoMax 零件數 {len(pmx_parts)} 不同'
    def t(bb): return tol if tol is not None else 1e-3 * max(bb[1]-bb[0], bb[3]-bb[2], bb[5]-bb[4]) + 1e-6
    if all(sp['vertex_bbox'] and _inside(sp['vertex_bbox'], pb, t(pb)) for sp, (_, pb) in zip(step_parts, pmx_parts)):
        return {pn: sp['name'] for sp, (pn, _) in zip(step_parts, pmx_parts)}, '依匯入順序對應，且每個 STEP 實體的頂點都落在對應零件包圍盒內'
    out = {}
    for pn, pb in pmx_parts:
        c = [sp for sp in step_parts if sp['vertex_bbox'] and _inside(sp['vertex_bbox'], pb, t(pb))]
        c = [sp for sp in c if all(_inside(sp['vertex_bbox'], qb, t(qb)) is False or qn == pn for qn, qb in pmx_parts)] or c
        if len(c) != 1: return None, f'零件 {pn} 找到 {len(c)} 個可能的 STEP 實體，無法唯一對應'
        out[pn] = c[0]['name']
    return out, '依包圍盒包含關係唯一對應'

def unique_names(mapping):
    seen, out = {}, {}
    for k, v in mapping.items():
        base = re.sub(r'[^\w\-一-鿿]+', '_', v).strip('_') or 'part'
        n = seen.get(base, 0); seen[base] = n + 1
        out[k] = base if n == 0 else f'{base}_{n+1}'
    return out
