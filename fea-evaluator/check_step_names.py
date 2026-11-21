# -*- coding: utf-8 -*-
"""
檢查 STEP 檔的零件名稱是否能正確讀出（不需要 PrePoMax）。
用法：python check_step_names.py [STEP 檔]　（不給檔名就取 input 資料夾最新的 .stp/.step）
輸出：檔案編碼、每個實體零件的名稱、所屬組件路徑、頂點包圍盒、是否含材料代號。
"""
import os, sys, glob, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import step_names, auto_rules as R

def main():
    if len(sys.argv) > 1: path = sys.argv[1]
    else:
        fs = sorted(glob.glob(os.path.join(HERE, 'input', '*.stp')) + glob.glob(os.path.join(HERE, 'input', '*.step')), key=os.path.getmtime)
        if not fs: print('input 資料夾沒有 STEP'); return
        path = fs[-1]
    parts = step_names.read_step_parts(path)
    mats = R.load_materials(os.path.join(HERE, 'rules', 'materials.json'))
    print(f'檔案：{path}')
    print(f'編碼：{step_names.FILE_ENCODING.get(path)}')
    print(f'實體零件數：{len(parts)}\n')
    print('| # | 名稱 | 組件路徑 | 材料代號 | 頂點包圍盒 X / Y / Z |')
    print('|---|---|---|---|---|')
    for i, p in enumerate(parts, 1):
        asg, miss = R.assign_materials([p['name']], mats)
        bb = p['vertex_bbox']
        bbs = ' / '.join(f'{bb[2*k]:.1f}~{bb[2*k+1]:.1f}' for k in range(3)) if bb else '（沒有頂點）'
        print(f"| {i} | {p['name']} | {' > '.join(p['path'][:-1])} | {asg.get(p['name'], '無 → 預設材料')} | {bbs} |")
    bad = [p['name'] for p in parts if '�' in p['name'] or '\\X' in p['name']]
    print('\n名稱解碼：' + ('⚠ 有無法解碼的名稱：' + ', '.join(bad) if bad else '全部可讀'))

if __name__ == '__main__':
    main()
