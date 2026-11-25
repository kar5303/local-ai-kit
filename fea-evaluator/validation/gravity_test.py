# 重力方向判斷的離線測試：多種組件、多種座標方向（gmsh 產生幾何＋面資料＋網格）
import gmsh, json, math, sys
import os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fea_eval, auto_rules as R

def rot(v, up):   # 把 Y 向上的座標轉成 up 指定的方向
    x, y, z = v
    return {'+Y': (x, y, z), '+Z': (x, -z, y), '+X': (y, -x, z), '-Y': (x, -y, -z), '-Z': (x, z, -y), '-X': (-y, x, z)}[up]

def build(parts, up, path):
    gmsh.initialize(); gmsh.option.setNumber('General.Terminal', 0); gmsh.model.add('t')
    o = gmsh.model.occ; tags = []
    for name, x0, x1, y0, y1, z0, z1 in parts:
        pts = [rot(p, up) for p in [(x0, y0, z0), (x1, y1, z1)]]
        lo = [min(pts[0][k], pts[1][k]) for k in range(3)]; hi = [max(pts[0][k], pts[1][k]) for k in range(3)]
        tags.append((name, o.addBox(lo[0], lo[1], lo[2], hi[0]-lo[0], hi[1]-lo[1], hi[2]-lo[2])))
    o.synchronize()
    femodel = []
    for i, (name, v) in enumerate(tags, 1):
        faces = []
        for _, s in gmsh.model.getBoundary([(3, v)], oriented=False):
            c = o.getCenterOfMass(2, s); a = o.getMass(2, s)
            uv = gmsh.model.getParametrizationBounds(2, s); pu = [(uv[0][0]+uv[1][0])/2, (uv[0][1]+uv[1][1])/2]
            n = gmsh.model.getNormal(s, pu); pt = gmsh.model.getValue(2, s, pu)
            if gmsh.model.isInside(3, v, [pt[k]+1e-3*n[k] for k in range(3)]): n = [-q for q in n]
            faces.append({'id': int(s), 'type': 'Plane', 'area': float(a), 'centroid': [float(q) for q in c], 'normal': [float(q) for q in n]})
        femodel.append({'name': name, 'id': i, 'faces': faces})
        gmsh.model.addPhysicalGroup(3, [v], i, name)
    gmsh.option.setNumber('Mesh.MeshSizeMax', 40); gmsh.model.mesh.generate(3)
    gmsh.option.setNumber('Mesh.SaveGroupsOfElements', 1); gmsh.write(path + '.inp'); gmsh.finalize()
    s = open(path + '.inp').read()
    for i, (name, _) in enumerate(tags, 1): s = s.replace(f'ELSET=Volume{i}\n', f'ELSET={name}\n')
    import re
    s = ''.join(b for b in re.split(r'(?=\*ELEMENT)', s) if not b.startswith('*ELEMENT') or 'C3D4' in b.split('\n')[0])
    s = s[:s.index('*ELSET')] if '*ELSET' in s else s
    open(path + '.inp', 'w').write(s)
    return {'femodel': femodel}

CASES = {
 # 名稱: (零件 [Y 向上座標], 預期向下 = -Y)
 '工具機C型（底座＋立柱＋主軸頭＋工作台）': [('BASE', -400, 400, 0, 200, -300, 300), ('COLUMN', -150, 150, 200, 1000, -300, -100), ('HEAD', -120, 120, 700, 950, -100, 150), ('TABLE', -250, 250, 200, 250, -50, 250)],
 '橋式（兩支座＋橫樑）': [('FOOT_L', -320, -280, 0, 60, -25, 25), ('BEAM', -280, 280, 30, 60, -25, 25), ('FOOT_R', 280, 320, 0, 60, -25, 25)],
 '桌子（四腳＋桌面）': [('LEG1', -290, -250, 0, 700, -190, -150), ('LEG2', 250, 290, 0, 700, -190, -150), ('LEG3', -290, -250, 0, 700, 150, 190), ('LEG4', 250, 290, 0, 700, 150, 190), ('TOP', -300, 300, 700, 730, -200, 200)],
 '高架台（底板＋高立柱＋頂板）': [('PLATE', -150, 150, 0, 20, -150, 150), ('POST', -30, 30, 20, 800, -30, 30), ('TOP', -100, 100, 800, 820, -100, 100)],
 '門型框（兩柱＋橫梁＋底座兩塊）': [('B1', -500, -300, 0, 40, -100, 100), ('B2', 300, 500, 0, 40, -100, 100), ('C1', -450, -350, 40, 900, -50, 50), ('C2', 350, 450, 40, 900, -50, 50), ('X', -450, 450, 900, 980, -50, 50)],
}
res = []
for cname, parts in CASES.items():
    for up in ['+Y', '+Z', '+X', '-Z']:
        topo = build(parts, up, '/tmp/gt')
        model = fea_eval.read_inp('/tmp/gt.inp'); fp = R.faces_by_part(topo); mp = R.mesh_parts(model)
        masses = {p: mp[p]['vol'] * 7.2e-9 for p in mp}
        ties = R.detect_ties(model, fp, mp, 0.5)
        g = R.detect_gravity(model, fp, mp, masses, ties, 0.5)
        exp = [-q for q in rot((0, 1, 0), up)]
        got = g.get('chosen', {}).get('down')
        okk = got is not None and R.dot(got, exp) > 0.99
        res.append(okk)
        print(f"{'✔' if okk else '✘'} {cname:28s} 向上={up}  預期向下={exp}  判斷={got}  旗標={g.get('flags')}")
print(f'通過 {sum(res)}/{len(res)}')
