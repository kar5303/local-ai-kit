# FEA 結構評估工具（PrePoMax / CalculiX）

讀取 PrePoMax 的 `.inp` 和 `.frd`，依零件計算強度、剛性貢獻、質量／重心／慣性，排出優化優先順序並輸出報告。
只用 Python 標準函式庫，不需安裝任何套件。判斷邏輯見 `評估邏輯規格.md`。

## 使用
```
python fea_eval.py Analysis-1.inp Analysis-1.frd
```
1. 第一次執行會產生 `fea_config.json`，填入：
   - `display_name`：零件中文名稱
   - `material_class`：`ductile`（結構鋼，Mises）或 `brittle`（灰鑄鐵，最大主應力）
   - `allowable`：鋼填降伏強度、鑄鐵填抗拉強度（MPa，依公司標準）
   - `moving_axis`：移動件填軸名（例如 "X"），固定件填 null
   - `safety_factor_required`、`weights`、`exclude_bc_radius`（mm）、`hotspot_cluster_distance`（mm）
   - `regions`：（選填）用座標範圍替區域命名
2. 再執行一次：
```
python fea_eval.py Analysis-1.inp Analysis-1.frd fea_config.json
```
3. 結果在 `Analysis-1_評估結果/`：
   - `report.md`：排名、質量慣性、熱區、計算方法、驗證
   - `parts.csv`、`hotspots.csv`：可用 Excel 開啟
   - `ai_summary.txt`：交給 AI 撰寫建議文字用

## PrePoMax 模型需求
- 每個零件是獨立的 Part（.inp 中為 `*Element, ELSET=零件名`）
- 材料要有彈性係數、蒲松比、**密度**
- Field Output 要有 `S`（應力）、`U`（位移）；`RF`／節點力用於驗證
- 目前支援元素：C3D4、C3D10、C3D8、C3D20（含 R）

## 已驗證（測試模型：400 mm 方管懸臂，自重）
| 項目 | 本工具 | 手算／對照 | 差異 |
|---|---|---|---|
| 質量 | 1.56 kg | ρ·A·L = 1.56 kg | 0% |
| 重心 | (-200, 25, 25) | 幾何中心 | 0% |
| Ixx（對重心） | 0.338 t·mm² | ρL(Iy+Iz) = 0.338 | 0% |
| Iyy（對重心） | 20.97 t·mm² | mL²/12 + ρL·Iy = 20.97 | 0% |
| 總應變能 | 3.3323e-8 N·mm | 外力功 ½ΣF·u = 3.3334e-8 | 0.03% |
| 總應變能 | 3.3323e-8 N·mm | 梁理論 w²L⁵/(40EI) = 3.294e-8 | 1.2%（梁理論不含剪切變形） |
| 最大位移 | 1.083e-5 mm | 梁理論 wL⁴/(8EI) = 1.076e-5 | 0.6% |
