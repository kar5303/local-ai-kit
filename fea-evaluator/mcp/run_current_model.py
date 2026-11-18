# -*- coding: utf-8 -*-
"""
一鍵分析：使用者自己在 PrePoMax 建好模型（匯入幾何/網格、材料、截面、邊界條件、負載、分析步）後執行。
本程式不改你的模型設定、不存檔，只做：
  1. 檢查 PrePoMax 目前模型是否具備求解所需項目
  2. 新增（或沿用）求解工作 AutoFEA 並求解
  3. 把 .inp/.frd 複製到 FEA-AI\\results\\<模型名稱>\\
  4. 用 fea_eval 評估（強度利用率、剛性貢獻、質量、風險佔比、熱區）
設定檔 results\\<模型名稱>\\fea_config.json：第一次執行會自動產生，容許值等請自己填，填完執行「只重新評估」即可。
用法：python run_current_model.py            （求解＋評估）
      python run_current_model.py --eval-only （不求解，用上次的結果重新評估）
"""
import os, sys, json, time, shutil, re
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import fea_eval

JOB = 'AutoFEA'
RESULTS = os.path.join(ROOT, 'results')
LOG = []
def log(s=''): print(s); LOG.append(str(s))
def jl(t):
    try: return json.loads(t)
    except Exception: return {'raw': t}
def names(lst): return [(x.get('name') if isinstance(x, dict) else str(x)) for x in (lst or [])]
def safe(n): return re.sub(r'[\\/:*?"<>|\s]+', '_', n).strip('_') or 'model'

def run(p, src, timeout=120):
    t, _ = p.call('run_python', source=src, timeout=timeout); d = jl(t)
    if d.get('failed'):
        log('---- PrePoMax Python 錯誤 ----'); log(d.get('error')); log(d.get('messages')); raise SystemExit(1)
    return (d.get('output') or '').strip(), d

def is_binary(path):
    with open(path, 'rb') as f: b = f.read(65536)
    return b'\x00' in b

def solve(outdir_holder):
    from pmx_mcp import PrePoMax
    p = PrePoMax()
    st = jl(p.call('get_status')[0]); m = st.get('model', {})
    log(f"1. PrePoMax 狀態：閒置={st.get('isIdle')}，模型={m.get('name')}，單位={m.get('unitSystem')}，未存檔修改={m.get('hasUnsavedChanges')}")
    if not st.get('isIdle') or st.get('isReadOnly'):
        log('   PrePoMax 忙碌中或為唯讀模式，停止。'); return None
    md = jl(p.call('read_model')[0])
    parts, mats, secs, steps, jobs = (names(md.get(k)) for k in ('parts', 'materials', 'sections', 'steps', 'jobs'))
    log(f'2. 模型內容：零件 {parts}')
    log(f'   材料 {mats}；截面 {secs}；分析步 {steps}；約束 {names(md.get("constraints"))}；接觸 {names(md.get("contact_pairs"))}')
    log(f'   分析步明細：{json.dumps(md.get("steps_detail"), ensure_ascii=False)[:1500]}')
    miss = [k for k, v in (('零件', parts), ('材料', mats), ('截面', secs), ('分析步', steps)) if not v]
    if miss:
        log(f'   模型缺少：{"、".join(miss)}。請在 PrePoMax 補齊後再執行。'); return None
    if m.get('unitSystem') in (None, 'Undefined'):
        log('   模型單位系統未定義，停止。'); return None
    if names(md.get('contact_pairs')):
        log('   注意：模型有接觸（非線性），剛性貢獻佔比只能當參考。')
    outdir = os.path.join(RESULTS, safe(m.get('name') or 'model')); os.makedirs(outdir, exist_ok=True)
    outdir_holder.append(outdir)

    log(f'3. 求解（工作名稱 {JOB}）')
    if JOB not in jobs:
        run(p, f"pmx.jobs.add('{JOB}')")
    p.call('run_job', name=JOB)
    status = None; t0 = time.time()
    while True:
        d = jl(p.call('get_job', name=JOB)[0]); status = (d.get('value') or {}).get('status')
        if status in ('OK', 'Killed', 'TimedOut', 'Failed', 'FailedWithResults'): break
        if time.time() - t0 > 6 * 3600: log('   超過 6 小時，停止等待'); break
        time.sleep(3)
    log(f'   求解狀態：{status}，耗時 {time.time()-t0:.0f} 秒')
    if status != 'OK':
        d = jl(p.call('get_job', name=JOB, include_output=True)[0]); log(str(d.get('value') or d)[-3000:]); return None
    wd, _ = run(p, "print(pmx.work_directory)")
    for ext in ('.inp', '.frd'):
        shutil.copy2(os.path.join(wd, JOB + ext), os.path.join(outdir, 'model' + ext))
    log(f'   結果已複製到 {outdir}')
    return outdir

def latest_outdir():
    if not os.path.isdir(RESULTS): return None
    ds = [os.path.join(RESULTS, d) for d in os.listdir(RESULTS) if os.path.exists(os.path.join(RESULTS, d, 'model.frd'))]
    return max(ds, key=os.path.getmtime) if ds else None

def evaluate(outdir):
    inp, frd = os.path.join(outdir, 'model.inp'), os.path.join(outdir, 'model.frd')
    log(f'4. 評估：{outdir}')
    if is_binary(frd):
        log('   .frd 是二進位格式，fea_eval 目前只讀 ASCII。請把 PrePoMax 的結果輸出格式改為 ASCII 後重新執行。'); return
    cfg_path = os.path.join(outdir, 'fea_config.json')
    model = fea_eval.read_inp(inp)
    if not os.path.exists(cfg_path):
        fea_eval.make_config_template(model, cfg_path)
        log(f'   第一次執行，已產生設定檔：{cfg_path}')
    cfg = json.load(open(cfg_path, encoding='utf-8'))
    for s in model['part_sets']:
        if s not in cfg['parts']:
            log(f'   設定檔沒有零件 {s}，以預設值加入（容許值未填）')
            cfg['parts'][s] = {'display_name': s, 'material_class': 'ductile', 'allowable': None, 'moving_axis': None}
    empty = [s for s, v in cfg['parts'].items() if v.get('allowable') in (None, 0)]
    if empty:
        log(f'   以下零件未填容許值，強度利用率不計算、風險佔比只依剛性：{empty}')
    r = fea_eval.evaluate(inp, frd, cfg)
    res_dir = os.path.join(outdir, '評估結果')
    fea_eval.write_outputs(r, res_dir, cfg)
    log(f"   單位系統：{model.get('unit_system')}；總應變能 {r['total_se']:.6g}；外力功檢查 {r['work_check']}")
    for i, k in enumerate(r['ranking'], 1):
        v = r['parts'][k]
        log(f"   {i}. {v['display_name']}：風險佔比 {v['risk_share']:.2f}%，剛性貢獻 {v['SE_share']:.2f}%，質量佔比 {v['mass_share']:.2f}%，利用率 {v['UR']}，建議：{'；'.join(v.get('advice', []))}")
    for w in r['warnings']: log(f'   ⚠ {w}')
    log(f'   報告：{os.path.join(res_dir, "report.md")}')
    log(f'   要改容許值、材料類別（ductile/brittle）、權重：編輯 {cfg_path}，再執行「只重新評估」。')

def main():
    eval_only = '--eval-only' in sys.argv
    holder = []
    try:
        if eval_only:
            od = latest_outdir()
            if not od: log('找不到之前的結果，請先執行求解＋評估。'); return
            holder.append(od); evaluate(od)
        else:
            od = solve(holder)
            if od: evaluate(od)
    finally:
        if holder:
            open(os.path.join(holder[0], 'run_log.txt'), 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')

if __name__ == '__main__':
    main()
