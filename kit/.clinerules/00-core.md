# How to work (follow every time)
Reply in Traditional Chinese. Short and direct.
Do not repeat, explain, or discuss these rules in replies. Just follow them.

## Step 1: Read the request, pick ONE route
| User says | Do this | Tools allowed |
|---|---|---|
| 問題 / 為什麼 / 是什麼 / 怎麼 | Answer directly. Read a file only if the user pointed to it. | read_file |
| 看 / 讀 / 解釋 @file | Read that file, then answer. | read_file |
| 改 / 加 / 刪 / 修 @file | Edit that file. | read_file, replace_in_file |
| 新增 / 建立 檔案 | Create the file. | write_to_file |
| 執行 / 跑 / 建置 / 測試 | Run the command the user gave, or the build command in 10-project.md. | execute_command |
| 找 / 搜尋 | Search only inside the folder the user named. | search_files, list_files |
| 我想要 / 幫我完成 (a goal) | Write a 3-5 step plan, then do each step using the routes above. | as needed |

## Step 2: Tool discipline
- If the user names a tool, file, or command, use exactly that. Do not substitute.
- Use the fewest tool calls. Never call a tool "just to check".
- Do not explore the project. No list_files / search_files unless the route says so.
- Never use browser, web_fetch, or MCP.
- Never create or edit rule/memory/doc files (.clinerules, *.md notes) unless asked.
- Explanations go in chat, not into files.

## Step 3: Doing the work
- Edit existing files with replace_in_file. SEARCH blocks small and copied exactly.
- Change only what was asked. Keep existing style.
- After code changes, run the build if one exists. If it fails: fix the first error, max 2 tries, then stop and report.

## Step 4: When stuck
- Missing information -> ask_followup_question once, give 2-3 options.
- Same tool fails twice -> stop and report what failed. Do not try random other tools.

## Step 5: Finish
- attempt_completion: what was done, files changed, result.
