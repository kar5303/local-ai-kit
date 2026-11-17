import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import { exec } from 'child_process';

const COPILOT_MODEL_KEY = 'localRunner.copilotModelId';
let output: vscode.OutputChannel;

type Role = 'system' | 'user' | 'assistant';
interface Msg { role: Role; content: string; }

export function activate(context: vscode.ExtensionContext) {
  output = vscode.window.createOutputChannel('Local Runner');
  context.subscriptions.push(
    output,
    vscode.commands.registerCommand('localRunner.runTask', () => runTask(context, 'task')),
    vscode.commands.registerCommand('localRunner.applyClipboard', () => runTask(context, 'clipboard')),
    vscode.commands.registerCommand('localRunner.selectModel', () => selectModel(context)),
    vscode.commands.registerCommand('localRunner.runBuild', async () => {
      const root = workspaceRoot();
      if (!root) { return; }
      output.show(true);
      const r = await runBuild(root);
      vscode.window.showInformationMessage(r.ok ? '建置成功' : '建置失敗，詳見 Local Runner 輸出');
    })
  );
}

export function deactivate() {}

// ---------- 共用 ----------

function cfg<T>(key: string): T {
  return vscode.workspace.getConfiguration('localRunner').get<T>(key) as T;
}

function workspaceRoot(): string | undefined {
  const f = vscode.workspace.workspaceFolders?.[0];
  if (!f) {
    vscode.window.showErrorMessage('請先用 VS Code 開啟專案資料夾（檔案 → 開啟資料夾）。');
    return undefined;
  }
  return f.uri.fsPath;
}

function log(s: string) { output.appendLine(s); }

function readText(p: string): string {
  try { return fs.readFileSync(p, 'utf8'); } catch { return ''; }
}

function safeResolve(root: string, rel: string): string | undefined {
  const full = path.resolve(root, rel.trim());
  const r = path.resolve(root);
  if (full !== r && !full.startsWith(r + path.sep)) { return undefined; }
  return full;
}

// ---------- 選模型 ----------

async function listOllamaModels(): Promise<string[]> {
  const url = cfg<string>('ollamaUrl').replace(/\/$/, '') + '/api/tags';
  const res = await fetch(url);
  if (!res.ok) { throw new Error(`HTTP ${res.status}`); }
  const data: any = await res.json();
  return (data.models ?? []).map((m: any) => m.name as string);
}

async function selectModel(context: vscode.ExtensionContext) {
  const provider = cfg<string>('provider');
  if (provider === 'copilot') {
    await pickCopilotModel(context, true);
    return;
  }
  let names: string[];
  try {
    names = await listOllamaModels();
  } catch (e: any) {
    vscode.window.showErrorMessage(`連不到 Ollama（${e?.message ?? e}）。請先開啟 Ollama 程式。`);
    return;
  }
  if (names.length === 0) {
    vscode.window.showErrorMessage('Ollama 裡沒有模型，請先用 ollama pull 下載。');
    return;
  }
  const pick = await vscode.window.showQuickPick(names, { placeHolder: '選擇 Ollama 模型（建議選 coder30b-32k）' });
  if (pick) {
    await vscode.workspace.getConfiguration('localRunner').update('ollamaModel', pick, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage(`已選擇：${pick}`);
  }
}

async function pickCopilotModel(context: vscode.ExtensionContext, forceAsk: boolean): Promise<vscode.LanguageModelChat | undefined> {
  const models = await vscode.lm.selectChatModels({ vendor: 'copilot' });
  if (models.length === 0) {
    vscode.window.showErrorMessage('找不到 Copilot 模型。');
    return undefined;
  }
  const saved = context.globalState.get<string>(COPILOT_MODEL_KEY);
  if (!forceAsk && saved) {
    const m = models.find(x => x.id === saved);
    if (m) { return m; }
  }
  const pick = await vscode.window.showQuickPick(
    models.map(m => ({ label: m.name, description: m.family, model: m })),
    { placeHolder: '選擇 Copilot 模型' }
  );
  if (!pick) { return undefined; }
  await context.globalState.update(COPILOT_MODEL_KEY, pick.model.id);
  return pick.model;
}

// ---------- 呼叫模型（Ollama / Copilot） ----------

async function chatOllama(messages: Msg[], token: vscode.CancellationToken): Promise<string> {
  const url = cfg<string>('ollamaUrl').replace(/\/$/, '') + '/api/chat';
  const model = cfg<string>('ollamaModel');
  const ctrl = new AbortController();
  const timeoutMs = (cfg<number>('requestTimeoutSeconds') || 1800) * 1000;
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const sub = token.onCancellationRequested(() => ctrl.abort());
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        messages,
        stream: true,
        options: { num_ctx: cfg<number>('numCtx') || 32768, temperature: 0.2 },
      }),
      signal: ctrl.signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`Ollama 回應錯誤 HTTP ${res.status}：${await res.text().catch(() => '')}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let reply = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) { break; }
      buf += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) { continue; }
        const obj = JSON.parse(line);
        if (obj.error) { throw new Error(obj.error); }
        const piece = obj.message?.content ?? '';
        reply += piece;
        output.append(piece);
        if (obj.done && obj.prompt_eval_count !== undefined) {
          log(`\n（輸入 ${obj.prompt_eval_count} tokens，輸出 ${obj.eval_count ?? '?'} tokens）`);
        }
      }
    }
    return reply;
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      throw new Error(token.isCancellationRequested ? '已取消' : '等待逾時，可調大 localRunner.requestTimeoutSeconds');
    }
    throw e;
  } finally {
    clearTimeout(timer);
    sub.dispose();
  }
}

async function chatCopilot(model: vscode.LanguageModelChat, messages: Msg[], token: vscode.CancellationToken): Promise<string> {
  // Copilot API 沒有 system 角色：併入第一則 user
  const lm: vscode.LanguageModelChatMessage[] = [];
  let sys = '';
  for (const m of messages) {
    if (m.role === 'system') { sys += m.content + '\n\n'; continue; }
    if (m.role === 'user') { lm.push(vscode.LanguageModelChatMessage.User((sys ? sys : '') + m.content)); sys = ''; }
    else { lm.push(vscode.LanguageModelChatMessage.Assistant(m.content)); }
  }
  const resp = await model.sendRequest(lm, {}, token);
  let reply = '';
  for await (const chunk of resp.text) { reply += chunk; output.append(chunk); }
  log('');
  return reply;
}

// ---------- 給模型的指示 ----------

function systemPrompt(): string {
  const extra = cfg<string>('extraRules') || '';
  return [
    'You edit code files. Reply in Traditional Chinese.',
    'First write at most 3 short lines explaining the change.',
    'Then output EVERY file you change or create in exactly this format:',
    '<<<FILE: relative/path/from/project/root>>>',
    '(the COMPLETE new file content. Never omit parts. Never write "..." or "rest unchanged")',
    '<<<END>>>',
    'Rules:',
    '- Do not wrap file content in ``` fences.',
    '- Only output files that really change. Change only what the task needs; keep existing code and style.',
    '- If you need a file you were not given, do NOT output FILE blocks; say which file you need.',
    '- If no change is needed, output no FILE blocks.',
    extra ? `- ${extra}` : '',
  ].filter(Boolean).join('\n');
}

interface FileEdit { rel: string; full: string; content: string; }

function parseEdits(root: string, text: string): FileEdit[] {
  const re = /<<<FILE:\s*(.+?)\s*>>>\r?\n([\s\S]*?)\r?\n?<<<END>>>/g;
  const edits: FileEdit[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const rel = m[1].trim().replace(/^\.?[\\/]/, '');
    const full = safeResolve(root, rel);
    if (!full) { log(`⚠ 略過工作區外的路徑：${rel}`); continue; }
    let content = m[2];
    // 模型若仍加了 ``` 外框，去掉
    content = content.replace(/^```[^\n]*\n/, '').replace(/\n```\s*$/, '');
    if (!content.endsWith('\n')) { content += '\n'; }
    edits.push({ rel, full, content });
  }
  return edits;
}

// ---------- 套用修改 ----------

async function previewAndApply(root: string, edits: FileEdit[]): Promise<boolean> {
  if (!cfg<boolean>('autoApply')) {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'local-runner-'));
    for (const e of edits) {
      const tmp = path.join(tmpDir, e.rel.replace(/[\\/]/g, '__'));
      fs.writeFileSync(tmp, e.content, 'utf8');
      let left = vscode.Uri.file(e.full);
      if (!fs.existsSync(e.full)) {
        fs.writeFileSync(tmp + '.empty', '', 'utf8');
        left = vscode.Uri.file(tmp + '.empty');
      }
      await vscode.commands.executeCommand('vscode.diff', left, vscode.Uri.file(tmp), `${e.rel}（左：原本　右：修改後）`, { preview: false });
    }
    const list = edits.map(e => `• ${e.rel}${fs.existsSync(e.full) ? '' : '（新檔案）'}`).join('\n');
    const ans = await vscode.window.showInformationMessage(
      `要套用以下 ${edits.length} 個檔案的修改嗎？\n${list}`, { modal: true }, '套用');
    if (ans !== '套用') { return false; }
  }
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const backupDir = path.join(root, '.local-runner-backup', stamp);
  for (const e of edits) {
    if (fs.existsSync(e.full)) {
      const b = path.join(backupDir, e.rel);
      fs.mkdirSync(path.dirname(b), { recursive: true });
      fs.copyFileSync(e.full, b);
    }
    fs.mkdirSync(path.dirname(e.full), { recursive: true });
    fs.writeFileSync(e.full, e.content, 'utf8');
    log(`✔ 已寫入：${e.rel}`);
  }
  log(`（原檔備份在 .local-runner-backup/${stamp}）`);
  return true;
}

// ---------- 建置 ----------

function runBuild(root: string): Promise<{ ok: boolean; out: string }> {
  const cmd = (cfg<string>('buildCommand') || '').trim();
  if (!cmd) {
    log('（未設定建置指令，略過建置。設定：localRunner.buildCommand）');
    return Promise.resolve({ ok: true, out: '' });
  }
  log(`▶ 執行建置：${cmd}`);
  const timeout = (cfg<number>('buildTimeoutSeconds') || 600) * 1000;
  return new Promise(resolve => {
    exec(cmd, { cwd: root, timeout, maxBuffer: 50 * 1024 * 1024, windowsHide: true }, (err, stdout, stderr) => {
      const out = `${stdout ?? ''}\n${stderr ?? ''}`;
      log(out.length > 20000 ? out.slice(-20000) : out);
      log(!err ? '✅ 建置成功' : `❌ 建置失敗`);
      resolve({ ok: !err, out });
    });
  });
}

/** 只取建置輸出中含 error 的行，省 context。 */
function extractErrors(out: string): string {
  const lines = out.split(/\r?\n/).filter(l => /error|錯誤|exception/i.test(l));
  const uniq = [...new Set(lines)].slice(0, 20).join('\n');
  return uniq || out.slice(-3000);
}

// ---------- 主流程 ----------

async function runTask(context: vscode.ExtensionContext, mode: 'task' | 'clipboard' = 'task') {
  const root = workspaceRoot();
  if (!root) { return; }

  const provider = cfg<string>('provider');
  let copilotModel: vscode.LanguageModelChat | undefined;
  let modelLabel: string;
  if (provider === 'copilot') {
    copilotModel = await pickCopilotModel(context, false);
    if (!copilotModel) { return; }
    modelLabel = `Copilot / ${copilotModel.name}`;
  } else {
    try {
      const names = await listOllamaModels();
      const want = cfg<string>('ollamaModel');
      if (!names.includes(want) && !names.includes(want + ':latest')) {
        vscode.window.showErrorMessage(`Ollama 裡找不到模型「${want}」。請用「Local Runner: 選擇模型」重新選。`);
        return;
      }
    } catch (e: any) {
      vscode.window.showErrorMessage(`連不到 Ollama（${e?.message ?? e}）。請先開啟 Ollama 程式。`);
      return;
    }
    modelLabel = `Ollama / ${cfg<string>('ollamaModel')}`;
  }

  let task: string;
  const files = new Set<string>();
  const active = vscode.window.activeTextEditor?.document;
  if (active && active.uri.scheme === 'file') { files.add(active.uri.fsPath); }

  if (mode === 'clipboard') {
    if (files.size === 0) {
      vscode.window.showErrorMessage('請先打開要修改的檔案，再執行「套用剪貼簿的修改」。');
      return;
    }
    const clip = (await vscode.env.clipboard.readText()).trim();
    if (!clip) {
      vscode.window.showErrorMessage('剪貼簿是空的。請先在 Copilot Chat 複製它的回答。');
      return;
    }
    const note = await vscode.window.showInputBox({
      prompt: `用 ${modelLabel} 套用剪貼簿內容（${clip.length} 字）到目前檔案`,
      placeHolder: '（選填）補充說明，例如：只套用 LoadMesh 的部分。直接按 Enter 略過',
      ignoreFocusOut: true,
    });
    if (note === undefined) { return; }
    task = [
      'Apply the change described below to the current file.',
      'The change was written by another assistant. It may contain explanations and code snippets.',
      'Merge the code into the correct place. Keep ALL other existing code unchanged.',
      'Do not redesign or add anything that is not in the change.',
      note ? `User note: ${note}` : '',
      '<<<CHANGE>>>',
      clip,
      '<<<END CHANGE>>>',
    ].filter(Boolean).join('\n');
  } else {
    const input = await vscode.window.showInputBox({
      prompt: `用 ${modelLabel} 執行任務`,
      placeHolder: '例：在 LoadMesh() 加入檔案不存在時的錯誤處理，其他函式不要動（多行內容請用 Ctrl+Alt+V 剪貼簿模式）',
      ignoreFocusOut: true,
    });
    if (!input) { return; }
    task = input;
  }

  const more = await vscode.window.showOpenDialog({
    canSelectMany: true, defaultUri: vscode.Uri.file(root),
    openLabel: '加入參考檔案（不需要就按取消）',
  });
  more?.forEach(u => files.add(u.fsPath));

  const fileBlocks = [...files].map(f => {
    const rel = path.relative(root, f).replace(/\\/g, '/');
    return `<<<CURRENT: ${rel}>>>\n${readText(f)}\n<<<END>>>`;
  }).join('\n\n');

  let treeText = '';
  if (cfg<boolean>('includeFileList')) {
    const uris = await vscode.workspace.findFiles('**/*',
      '{**/bin/**,**/obj/**,**/node_modules/**,**/.git/**,**/packages/**,**/.vs/**,**/.local-runner-backup/**}', 150);
    treeText = '=== Project files ===\n' + uris.map(u => path.relative(root, u.fsPath).replace(/\\/g, '/')).sort().join('\n') + '\n\n';
  }

  const userMsg = `${treeText}=== Current files ===\n${fileBlocks || '(none)'}\n\n=== Task ===\n${task}`;

  // context 預估（約 3 字元 = 1 token）
  const estTokens = Math.round((systemPrompt().length + userMsg.length) / 3);
  const numCtx = cfg<number>('numCtx') || 32768;
  if (provider !== 'copilot' && estTokens > numCtx * 0.45) {
    const go = await vscode.window.showWarningMessage(
      `送出內容約 ${estTokens} tokens，接近 context 上限 ${numCtx}（模型還要輸出完整檔案）。建議少選檔案或拆小任務。仍要繼續？`,
      { modal: true }, '繼續');
    if (go !== '繼續') { return; }
  }

  const messages: Msg[] = [
    { role: 'system', content: systemPrompt() },
    { role: 'user', content: userMsg },
  ];
  const maxIter = Math.max(0, cfg<number>('maxIterations') ?? 2);

  output.show(true);
  log(`\n========== 新任務（${new Date().toLocaleString()}）==========`);
  log(`模型：${modelLabel}`);
  log(`任務：${task}`);
  log(`檔案：${[...files].map(f => path.relative(root, f)).join(', ') || '（無）'}`);

  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: 'Local Runner', cancellable: true },
    async (progress, token) => {
      for (let round = 1; round <= maxIter + 1; round++) {
        if (token.isCancellationRequested) { log('已取消'); return; }
        progress.report({ message: `第 ${round} 輪：模型思考中（本機模型會比較久）…` });
        log(`\n--- 第 ${round} 輪：模型回覆 ---`);

        let reply: string;
        try {
          reply = copilotModel
            ? await chatCopilot(copilotModel, messages, token)
            : await chatOllama(messages, token);
        } catch (e: any) {
          const msg = String(e?.message ?? e);
          log(`\n❌ 模型呼叫失敗：${msg}`);
          vscode.window.showErrorMessage(`模型呼叫失敗：${msg}`);
          return;
        }
        messages.push({ role: 'assistant', content: reply });

        const edits = parseEdits(root, reply);
        if (edits.length === 0) {
          log('\n（模型沒有提出檔案修改）');
          vscode.window.showInformationMessage('模型沒有提出檔案修改，請看 Local Runner 輸出的說明。');
          return;
        }

        progress.report({ message: `第 ${round} 輪：等待你確認修改…` });
        if (!await previewAndApply(root, edits)) { log('你取消了套用。'); return; }

        progress.report({ message: `第 ${round} 輪：建置中…` });
        const build = await runBuild(root);
        if (build.ok) {
          vscode.window.showInformationMessage(`✅ 完成（第 ${round} 輪）`);
          return;
        }
        if (round > maxIter) {
          vscode.window.showWarningMessage(`已自動修正 ${maxIter} 次仍失敗，請看輸出（原檔已備份）。`);
          return;
        }
        messages.push({
          role: 'user',
          content: `Build failed. Errors:\n${extractErrors(build.out)}\n\nFix ONLY these errors. Output the changed files in the same FILE format with COMPLETE content.`,
        });
      }
    }
  );
}
