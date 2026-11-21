# M-003：與 M001 幾何相同，但用「子組件＋旋轉/平移放置」與中文名稱輸出 STEP（模擬 CAD 組件）
import cadquery as cq
from cadquery import Location, Vector
# 底座（含墊腳），在零件自身座標建：底面在 z=0，之後旋轉成 Y 向上
base = cq.Workplane('XY').box(800, 600, 200, centered=(True, True, False)).translate((0, 0, 50))
for sx in (-1, 1):
    for sy in (-1, 1):
        base = base.union(cq.Workplane('XY').box(80, 80, 50, centered=(True, True, False)).translate((sx*360, sy*260, 0)))
column = cq.Workplane('XY').box(300, 200, 800)            # 中心在原點
head = cq.Workplane('XY').box(240, 250, 250)              # 中心在原點
spindle = cq.Workplane('XY').circle(50).extrude(150)      # 軸沿 +Z，z 0~150
table = cq.Workplane('XY').box(500, 300, 50)
rx = Location(Vector(0, 0, 0), Vector(1, 0, 0), -90)      # 繞 X 轉 -90°：局部 +Z → 全域 +Y，局部 +Y → 全域 -Z
asm = cq.Assembly(name='MACHINE')
asm.add(base, name='底座_FC300', loc=rx)                   # base 局部 y → 全域 -z（對稱，不影響）
asm.add(column, name='立柱_FC300', loc=Location(Vector(0, 650, -200)) * rx * Location(Vector(0, 0, 0)))
sub = cq.Assembly(name='主軸頭組')
sub.add(head, name='主軸頭_FC300', loc=Location(Vector(0, 0, 0)))
sub.add(spindle, name='主軸_S45C', loc=Location(Vector(0, -125, 25)) * Location(Vector(0, 0, 0), Vector(1, 0, 0), 90))  # 局部 +Z → 子組件 -Y
asm.add(sub, name='主軸頭組', loc=Location(Vector(0, 825, 25)))
asm.add(table, name='工作台_FC250', loc=Location(Vector(0, 275, 100)) * rx)
asm.save('M003.step')
