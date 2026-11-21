# M-004：單一零件（法蘭立柱，Y 向上）
import cadquery as cq
flange = cq.Workplane('XZ').rect(400, 400).extrude(-40)            # y 0~40
col = cq.Workplane('XZ').workplane(offset=-40).rect(200, 200).extrude(-800)   # y 40~840
part = flange.union(col)
asm = cq.Assembly(name='M004'); asm.add(part, name='法蘭立柱_FC300')
asm.save('M004.step')
