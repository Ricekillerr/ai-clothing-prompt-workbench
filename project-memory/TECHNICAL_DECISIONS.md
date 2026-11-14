# 项目记忆：技术决策记录

最后更新：2026-11-14

## 1. 跨平台稳定性必须直接测试真实行为

在既有 V0.6.5 候选代码上实际执行测试，而不是只审阅源码：

- 云主机运行完整单元测试，以 Linux 常规环境验证通用逻辑；
- Windows 本机从项目根目录运行 `python -m unittest discover -s tests -t . -v`，覆盖本地导入、文件句柄、换行和 BAT 启动；
- BAT 必须在真实 Windows 服务环境验证启动、版本接口、心跳关闭和进程清理；
- 真机启动自动化必须使用 `PROMPT_ATELIER_NO_BROWSER=1`，避免测试页面心跳取消关闭请求；正常用户启动仍默认打开浏览器；
- 符号链接测试如果因 Windows 权限跳过，必须明确记录，不能算通过。

## 2. 测试夹具也受生产数据完整性约束

### Node 前端测试

`test_frontend_progress.py` 原来把完整 Node 脚本放进 `node -e` 命令行，在 Windows 可能超过命令行长度限制（`WinError 206`）。现改为临时 `.js` 文件执行，不改变前端测试断言。

组合器分页测试夹具补齐 `composeNow`、`copyText`、`saveDraft` 三个函数桩；业务代码不变，30/30/20 分页、跨页选择和搜索重置断言保持完整。

### AI 提示词字节

提示词原文保护必须验证真实字节。测试夹具统一用 `write_bytes(original.encode("utf-8"))`，避免 Windows 文本写入自动把 LF 转成 CRLF，从而改变正文和 SHA-256。

### SQLite 文件占用

涉及故意损坏数据库或未提交事务的测试，使用 `try/finally` 显式关闭连接，防止 Windows 下临时目录因数据库/WAL 句柄无法清理。

## 3. Windows BAT 必须保持 CRLF 原始字节

真实测试确认，UTF-8 中文 BAT 在 LF-only 状态下被 CMD 错误解析，转换 CRLF 后启动、版本、心跳与关闭正常。

因此：

- 新增 `.gitattributes`：`*.bat -text`；
- BAT 使用 UTF-8 无 BOM + CRLF；
- 不再对 BAT 做 Git 自动换行归一化；
- `test_windows_runtime.py` 直接读取原始字节，检查 CRLF、无 BOM 和 UTF-8 往返；
- Windows 用户双击启动仍默认打开浏览器。

## 4. 更新清单必须保留 BAT 哈希和既有发布元数据

`生成更新清单.py` 的策略：

- 非 BAT 文本允许把 CRLF 归一化为 LF 后计算哈希；
- BAT 直接对原始字节计算 SHA-256；
- 保留现有 notes、发布文件集合和哈希排列；
- 新增发布文件按当前发布列表追加；
- 连续两次生成结果字节一致。

当前 BAT SHA-256：

`9391f5e9afa52cc9e7b8e8c32bcea5ffc596a475061521c4ad990f911673e654`

当前 manifest SHA-256：

`b0564732c9cad23f09f1ca1456c3d03190624974091ec696e99937e45b97361e`

## 5. 用户资料与更新事务边界不变

- 数据库、参考图片、个人设置、API Key 不得进入 Git 提交；
- 程序更新不能覆盖用户资料；
- AI 原文保护、哈希校验和人工审核不回退；
- 更新清单的 notes 不因代码生成器再次运行而被覆盖。

## 6. 模型与 Agent 路线是规划，不伪装为已接入

用户提供的“推荐技术组合”截图中的 Kimi Code、Kimi K3.1 Pro、DeepSeek V4.1 Flash、GPT-6、DeepSeek/GLM、Seedream 5.5 等名称记录为未来规划。

当前真实能力仍是：

- `ai_analyzer.py` 使用单个 OpenAI 兼容 `/chat/completions` 接口；
- 默认 endpoint 为 `https://api.openai.com/v1`；
- 默认模型字段为 `gpt-4o-mini`；
- 支持 1–8 张图片和 JSON object 输出；
- API Key 只用于当次请求，不保存；
- 远程端点必须 HTTPS，阻止重定向转发凭据。

Seedream 5.5 应通过独立图片生成/编辑适配器实现，不能冒充现有分析器能力。真实模型 ID、账号和 API 权限需要后续实测。

## 7. 持续服务与浏览器生命周期的取舍

自动测试需要无界面模式，但真实用户仍依赖双击后自动打开页面。因此增加环境变量开关，而不是全局禁用浏览器：

- `PROMPT_ATELIER_NO_BROWSER=1`：不调度自动打开浏览器；
- 未设置：保留原默认行为；
- 服务、心跳、关闭和 SQLite 逻辑不变。

## 8. 原生 Git 网络失败时，连接器推送必须明确提交语义

用户已明确授权提交并推送开发分支。Windows 原生 Git 在禁用凭据交互后仍无法连接 GitHub；GitHub OAuth 随后实际验证成功。

因此使用 GitHub 连接器按文件创建远端提交，并遵守：

- 只写 `release/v0.6.5-stabilization`，不修改 `main`；
- 不声称连接器提交保留本地两个 Git 提交的 SHA；
- 本地提交 `aabb5d7018f40d13e923152e8ec77d4b8abba85d`、`f38c98cf7c1f7f89b2ab0e6dc703a0af1467c0f8` 和 bundle 继续作为原始备份；
- BAT 以 CRLF 字符串提交，其他文本保持既有换行和编码；
- 推送后必须回读远端提交和文件内容，并重新运行修复后的完整 Actions；
- 连接器成功不代表 GitHub Actions 已通过，也不代表 V1.0 门禁完成。
