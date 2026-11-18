# 產生 V-007 兩段方管網格（C3D20，兩個獨立零件，介面於 x=-200）
NAT=[(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1),
     (0,-1,-1),(1,0,-1),(0,1,-1),(-1,0,-1),(0,-1,1),(1,0,1),(0,1,1),(-1,0,1),
     (-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)]
def tube(x0,x1,nx,outer,inner,y0=10.0):
    w=(outer-inner)/2; ys=[y0,y0+w,y0+w+inner,y0+outer]
    xs=[x0+(x1-x0)*i/nx for i in range(nx+1)]
    els=[]
    for i in range(nx):
        for j in range(3):
            for k in range(3):
                if j==1 and k==1: continue
                els.append(((xs[i],xs[i+1]),(ys[j],ys[j+1]),(ys[k],ys[k+1])))
    return els
nodes={}; nid=[0]
def node(p):
    key=tuple(round(c,6) for c in p)
    if key not in nodes: nid[0]+=1; nodes[key]=nid[0]
    return nodes[key]
def write_part(els,start_eid,lines,elset):
    lines.append(f'*Element, Type=C3D20, Elset={elset}')
    eid=start_eid
    for (xa,xb),(ya,yb),(za,zb) in els:
        conn=[]
        for a,b,c in NAT:
            conn.append(node(((xa+xb)/2+a*(xb-xa)/2,(ya+yb)/2+b*(yb-ya)/2,(za+zb)/2+c*(zb-za)/2)))
        lines.append(f'{eid}, '+', '.join(map(str,conn[:15]))+',')
        lines.append(', '.join(map(str,conn[15:])))
        eid+=1
    return eid
# 兩個零件的節點要分開（介面不共用節點）：分兩次建立節點字典
out=['** V-007: two tubes tied at x=-200. Part A x[-400,-200] 30x30/20x20; Part B x[-200,0] 30x30/24x24']
elines=[]
e=write_part(tube(-400,-200,10,30,20),1,elines,'PartA')
nodesA=dict(nodes); nodes.clear()
offset=nid[0]
elB=[]; e2=write_part(tube(-200,0,10,30,24),e,elB,'PartB')
out.append('*Node')
for k,v in sorted(nodesA.items(),key=lambda t:t[1]): out.append(f'{v}, {k[0]}, {k[1]}, {k[2]}')
for k,v in sorted(nodes.items(),key=lambda t:t[1]): out.append(f'{v}, {k[0]}, {k[1]}, {k[2]}')
out+=elines+elB
open('V007_mesh.inp','w').write('\n'.join(out)+'\n')
print('nodes',nid[0],'elements',e2-1)
