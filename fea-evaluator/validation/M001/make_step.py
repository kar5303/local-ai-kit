# 測試機台（立式，座標 Y 向上 → 重力 -Y；程式不會被告知）
import gmsh
gmsh.initialize(); gmsh.option.setNumber('General.Terminal', 0)
gmsh.model.add('M001'); o = gmsh.model.occ
def box(x0,x1,y0,y1,z0,z1): return o.addBox(x0,y0,z0,x1-x0,y1-y0,z1-z0)
base = box(-400,400,50,250,-300,300)
feet = [box(sx*400-(80 if sx>0 else 0), sx*400+(0 if sx>0 else 80), 0,50, sz*300-(80 if sz>0 else 0), sz*300+(0 if sz>0 else 80)) for sx in (-1,1) for sz in (-1,1)]
b, _ = o.fuse([(3,base)], [(3,f) for f in feet]); base = b[0][1]
column = box(-150,150,250,1050,-300,-100)
head = box(-120,120,700,950,-100,150)
spindle = o.addCylinder(0,550,50, 0,150,0, 50)
table = box(-250,250,250,300,-50,250)
o.synchronize()
names = {base:'BASE_FC300', column:'COLUMN_FC300', head:'HEAD_FC300', spindle:'SPINDLE_S45C', table:'TABLE_FC250'}
for t,n in names.items(): gmsh.model.setEntityName(3,t,n)
gmsh.write('M001.step'); gmsh.finalize()

# gmsh 不會把名稱寫進 STEP，依實體順序補上 PRODUCT 名稱
import re
order = [n for t, n in sorted(names.items())]
s = open('M001.step', encoding='utf-8').read()
k = [0]
def rep(m):
    i = int(m.group(1)) - 1; return f"PRODUCT('{order[i]}','{order[i]}'"
s = re.sub(r"'Open CASCADE STEP translator 7\.8 1\.(\d)'", lambda m: "'" + order[int(m.group(1)) - 1] + "'", s)
open('M001.step', 'w', encoding='utf-8').write(s)
