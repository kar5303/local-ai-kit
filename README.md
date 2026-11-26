> **FEA 全自動分析（fea-evaluator）**：下載後雙擊最外層的 `START_FEA.bat`（或 `fea-evaluator\START_FEA.bat`），依選單操作。說明見 `fea-evaluator/使用說明-全自動分析.md`。

# 純本機 AI 寫程式環境：建置流程

目標：只用本機 Ollama 模型，在 VS Code 裡「下指令 → 自動改檔 → 自動建置 → 失敗自動修正」。資料不離開電腦，不用 token 費用。

---

## 第 1 步：安裝與更新
1. 安裝或更新 **Ollama**（ollama.com，Windows 版），命令提示字元執行 `ollama -v` 確認。
2. 安裝或更新 **VS Code** 與 **Cline**（官方版，有藍色勾勾）。

## 第 2 步：設定 Ollama 環境變數
開始 → 搜尋「編輯您帳戶的環境變數」→ 使用者變數 → 新增：

| 變數名稱 | 值 |
|---|---|
| OLLAMA_CONTEXT_LENGTH | 32768 |
| OLLAMA_FLASH_ATTENTION | 1 |
| OLLAMA_KEEP_ALIVE | 60m |

完成後：右下角羊駝圖示按右鍵 → Quit Ollama → 重新開啟 Ollama。

## 第 3 步：下載模型並建立 32k 版本
1. 把本套件的 `ollama-models` 資料夾複製到 `C:\ollama-models`
2. 命令提示字元執行：
```
ollama pull qwen3-coder:30b
cd C:\ollama-models
ollama create coder30b-32k -f Modelfile
```
3. （需要看圖時才做）
```
ollama pull qwen3-vl:30b
ollama create vl30b-32k -f Modelfile-vl
```
4. 驗證：`ollama show coder30b-32k` → Capabilities 有 tools、Parameters 有 num_ctx 32768

## 第 4 步：測試模型
```
ollama run coder30b-32k --verbose "用三句話說明什麼是 C# 的 interface"
```
另開視窗執行 `ollama ps`，確認 CONTEXT = 32768。記下 prompt eval rate / eval rate（速度基準）。

---

## 第 5 步：安裝 Local Runner（主力：最穩定）
1. VS Code → 延伸模組 → 右上「⋯」→「從 VSIX 安裝」→ 選 `local-runner-0.3.0.vsix`
2. Ctrl+, 搜尋 `localRunner`，設定：
   - buildCommand：`dotnet build 你的方案.sln -c Release`
   - ollamaModel：`coder30b-32k`（或用 Ctrl+Shift+P →「Local Runner: 選擇模型」）
   - 其他保持預設
3. 使用：開啟檔案 → Ctrl+Alt+R → 輸入任務 → 可加選參考檔案 → 看差異 → 按「套用」→ 自動建置/修正
4. 過程：輸出面板 → 下拉選「Local Runner」
5. 還原：專案內 `.local-runner-backup/`

## 第 6 步：設定 Cline（需要探索、多步驟時使用）
1. Cline 齒輪 → API Provider：Ollama
2. Model：`coder30b-32k`；Model Context Window：32768；Request Timeout：1800000
3. （要看圖）勾選 Use different models for Plan and Act：Plan = vl30b-32k、Act = coder30b-32k
4. 關閉：Browser、所有 MCP；有 compact prompt 選項就打開
5. Auto-approve：只勾 Read
6. 把本套件的 `.clinerules` 資料夾複製到專案根目錄，修改 `10-project.md` 標 `<-` 的三行

---

## 第 7 步：推薦流程「Copilot 想 + Local Runner 套用」
設計、修改、優化這類需要判斷的工作，交給 Copilot；套用到檔案、建置、修小錯交給本機模型。
1. 在 **Copilot Chat** 說明需求，請它給修改後的程式碼（可貼上目前檔案內容給它看）
2. 在 Copilot 的回答上 **複製整段回答**（說明＋程式碼都可以）
3. 在 VS Code **打開要修改的檔案**
4. 按 **Ctrl+Alt+V**（Local Runner: 套用剪貼簿的修改）
5. 補充說明可留空，直接按 Enter；參考檔案不需要就按取消
6. 看差異預覽 → 確認右邊檔案完整、只改了該改的地方 → 按「套用」
7. 自動建置；失敗會自動請本機模型修正

小提醒：
- 一次只套用一個檔案的修改；Copilot 改了多個檔案，就分次打開各檔案、各複製對應那段
- 差異預覽中若右邊檔案少了一大段，按取消，不要套用

## 第 8 步：日常使用
| 情況 | 用哪個 |
|---|---|
| 設計、優化、需要判斷 | **Copilot Chat 想 → Ctrl+Alt+V 套用** |
| 改一兩個檔案、修建置錯誤（純本機） | **Local Runner**（Ctrl+Alt+R） |
| 需要它自己找檔案、多步驟、看圖 | **Cline**（先 Plan 再 Act） |
| 單純問問題 | Ollama 聊天視窗或 Cline 問答 |

固定習慣：
1. 開始前 `git commit`
2. 一次一件小事，明確指定檔案
3. 任務完成就開新任務
4. 修改都看過差異再套用

## 驗收
連續 3 個小任務（例如：為某函式加註解、加錯誤處理、修一個建置錯誤）都成功 → 設定完成。

## 疑難排解
| 症狀 | 處理 |
|---|---|
| 連不到 Ollama | 開啟 Ollama 程式（右下角羊駝圖示） |
| ollama ps 顯示 CONTEXT 4096 | 確認選的是 coder30b-32k；重開 Ollama |
| 等待逾時 | 調大 localRunner.requestTimeoutSeconds / Cline Request Timeout |
| 送出內容接近上限的警告 | 少選檔案、拆小任務 |
| 模型輸出被截斷、檔案不完整 | 檔案太大：一次只處理一個檔案，或把大檔案拆小 |
| Cline 亂找檔案 | 用 @ 指定檔案；確認 .clinerules 已放好 |
| 電腦很卡 | 關掉 Abaqus 等大型程式 |
