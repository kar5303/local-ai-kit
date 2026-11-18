# FEA project rules
Reply in Traditional Chinese. State only facts from tool results; no guesses.

## Routes
| User says | Do | Tools |
|---|---|---|
| 評估 / 判斷 結果 | Run `python fea_eval.py <inp> <frd> fea_config.json` in the result folder, then read report.md | execute_command, read_file |
| 用 PrePoMax 建模 / 分析 / 求解 | First call prepomax `get_guide`, follow it | prepomax MCP |
| 改程式 | Edit only the named file, then run the example validation | read_file, replace_in_file, execute_command |

## Never
- Never change allowable values, weights, or safety factors in fea_config.json unless the user gives the numbers.
- Never delete .inp/.frd/.pmx files.
- No internet, browser, or other MCP servers.
