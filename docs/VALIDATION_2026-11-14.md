# Prompt Atelier V0.6.5 — 2026-11-14 验证报告

## 结论

V0.6.5 候选版稳定化、双环境自动化验证和开发分支 CI 收尾已完成。以下结果均针对统一 `test_*.py` 全量发现口径；跳过项不计为通过。

| 环境 | 运行数 | 通过 | 跳过 | 失败 / 错误 | 结果 |
|---|---:|---:|---:|---:|---|
| 云主机 Linux | 176 | 174 | 2（真实 Windows BAT、Windows 短路径） | 0 / 0 | OK，12.674 秒 |
| 用户 Windows / Python 3.13 | 176 | 172 | 4（本机符号链接权限/环境） | 0 / 0 | OK，24.234 秒 |
| 用户 Windows / 便携 Python 3.12.10 | 176 | 172 | 4（本机符号链接权限/环境） | 0 / 0 | OK，23.820 秒 |

- 新增 Windows 8.3 短路径回归在用户 Windows 两种 Python 运行时中均实际执行并通过，不是跳过。
- Windows 本机较早一次修复后调用因 `unittest discover -t` 要求起始目录可导入而在测试发现前终止，不计为项目测试失败；改为显式项目导入路径后得到上表结果。
- 这些结果证明候选源码和隔离测试通过，不等于真实用户资料、真实模型服务或完整人工桌面验收已完成。

## GitHub Actions 最终证据

- **#577 / run 43574495341**：最终文档头 `e93343065226cedc652fcd667c176ab29ff670e4`，Success，1 分 1 秒；Linux `validate` 17 秒、Windows 全量 job 57 秒、release gate 5 秒，最终分支头完整工作流全绿。
- **#573 / run 43572935991**：提交 `7527bd242f6868eff80d461ba88e7795946031b0`，Success，47 秒。Linux `validate` 21 秒、Windows 全量 job 44 秒、`Release source and channel gate` 6 秒，全部通过。该提交加入独立短路径回归，工作流按 `test_*.py` 发现当前 176 项套件。
- **#572 / run 43572734163**：提交 `1061f1c7c3cb5364cbe5909c4ffb1efe38fe5d6c`，Success，49 秒。恢复后的统一全量工作流首次全绿，对应加入短路径回归前的 175 项口径。
- **#571 / run 43572633057**：存储短路径定向用例已通过；清单定向用例仍失败，因为该运行早于 LF checkout 策略提交。
- **#570 / run 43571636415**：两个定向用例的公开注解首次给出真实 traceback，分别定位短路径别名误判和 checkout 换行导致的清单哈希变化。
- **#569 / run 43571359456**：8 个精确用例 job 中只剩上述两项失败。
- **#568 / run 43571120135**：13 个测试文件 job 中只有 `test_storage.py` 和 `test_upgrade_bridge.py` 失败，其余 11 个文件通过。
- **#567 / run 43570864671**：直接全量 UTF-8 Windows 仍失败，Linux 与 release gate 通过。
- **#560 / run 43567340207**：曾将 Windows 唯一错误定位为 `test_main_browser_launch_respects_headless_environment`。
- 中间迭代 #557–#559、#561–#566 属于手工同步和诊断过程，不作为最终通过证据。详细日志仍需登录，本报告只采用实际读取的公开运行摘要、公开注解和本地完整测试日志。

## 已验证修复

1. **组合器与 Windows 测试夹具**：Node 模拟环境补齐 `composeNow`、`copyText`、`saveDraft`；长 `node -e` 改临时 `.js`，避免 WinError 206；提示词按原始字节写入；SQLite 测试连接显式关闭。
2. **BAT 与清单字节策略**：BAT 为 UTF-8 无 BOM、35 个 CRLF、0 个裸 LF；`.gitattributes` 保留 `*.bat -text`。清单生成器对 BAT 按原始字节哈希，非 BAT 文本归一化换行，保留 notes、发布文件及哈希顺序，连续生成字节一致。
3. **浏览器自动化隔离**：`PROMPT_ATELIER_NO_BROWSER=1` 只抑制测试自动打开页面，正常双击 BAT 默认打开浏览器的行为不变。
4. **Windows 控制台编码**：真实 ASCII strict 控制台回归复现全角冒号触发 `UnicodeEncodeError` 后，入口对支持 `reconfigure` 的 stdout/stderr 使用 UTF-8、`errors="replace"`；不支持时安全跳过。独立 `tests/test_console_output.py` 使用真实 `main()` 验证旧代码失败、修复后通过。
5. **Windows 短路径来源等价**：GitHub Runner 的 8.3 临时路径别名曾被误判为“AI 分析来源文件路径已发生变化”。`_verified_ingest_source_path()` 现对已存在文件使用 `os.path.samefile(raw_path, resolved)` 判断同一文件；符号链接/junction 拒绝、来源边界、扫描记录、类型与 SHA-256 校验全部保留。独立 `tests/test_windows_short_path.py` 通过 `GetShortPathNameW` 覆盖真实别名。
6. **跨平台 checkout 字节一致**：除 BAT 保持 `-text` 外，Python、HTML、JSON、TXT、YML、Markdown 和 PowerShell 文本固定 `text eol=lf`，避免 Windows checkout 的 CRLF 转换改变发布文件 SHA-256。
7. **统一 CI 门禁**：诊断矩阵已移除；当前工作流运行 Linux 全量、Windows 全量、前端语法、清单哈希、PowerShell 语法和 release source/channel gate。

## 完整性与同步证据

- 远端开发分支：`release/v0.6.5-stabilization`。
- 重复实例修复代码提交：`ddf6630dfffd52a76aa41536cdfc0f5dc9e1d14e`（远端 CI 待核对）。
- 统一工作流提交：`1061f1c7c3cb5364cbe5909c4ffb1efe38fe5d6c`。
- 存储与清单修复提交：`96aa27f3a3f466023bf2adb746da3aeb1bd02acf`。
- `storage.py` SHA-256：`45c2ec08730be950e079474a9f83a7ccc8bf1d093cae08177875b8a495c243f6`；本地、远端原始字节与清单一致。
- `update_manifest.json` SHA-256：`6a424277292f03a87beee2761ea2a8d0839f820e766becd53ff57eb3e296b18d`（已随 `ddf6630` 推送，远端 Git 对象与本地一致）。
- `ai_prompt_workbench.py` SHA-256：`bbade7d8d5d08a342e4df588c61c169396dcd81b82d1fc572645043e27b46813`（已随 `ddf6630` 推送）。
- BAT SHA-256：`9391f5e9afa52cc9e7b8e8c32bcea5ffc596a475061521c4ad990f911673e654`。
- Windows 权威目录：`D:\豆-提示词工作台`，已同步存储、清单、checkout 策略、统一工作流和独立短路径测试；同步前文件备份在会话目录 `ci-fixes-backup`。
- 两个原始本地提交与连接器产生的远端 SHA 不同；原始提交 bundle 已双环境验证并保留，不能把本地 `ahead 2` 描述为同一提交已同步。

## 历史档案接续

2026-11-14 新增的“接近完全版”历史档案已完整读取，作为项目目标、早期版本、故障史、原始代码证据和 GitHub 固定提交索引的补充来源。档案中的旧组合器失败、168 项 CI 和旧 manifest 状态属于历史基线，不能覆盖当前源码与本报告的实际验证结果。

## 2026-11-14 隔离实机与 HTTP 验收（本地新增证据）

以下结果来自用户 Windows 上的真实 BAT 和 Python 服务进程，以及云主机 Linux 的交叉回归；Windows `USERPROFILE` 指向会话隔离目录，没有读取或改写真实用户 SQLite、参考图片或配置，也没有配置模型 Key。修复代码已推送为 `ddf6630`，新提交的 CI 结果仍需单独核对。

- **真实 BAT + 隔离 HTTP 主流程通过**：首页返回 200；`/api/version` 返回 0.6.5；写入一条明确标记的验收案例及模块后重新读取，105 字节原始提示词的首尾空格、换行和 `@图片1` 逐字一致；按 SKU 以 0.8 阈值检索命中 1 条已验证案例；完整备份 ZIP 为 5,494 字节；临时修改案例后经 `/api/backup/restore` 恢复，原提示词和“已验证成功”状态均还原。
- **正常关闭通过**：`/api/client/close` 后启动进程退出，8765 端口监听数为 0。运行日志确认数据目录为会话隔离路径，不是真实用户目录。
- **中文加空格路径通过**：将九个清单运行文件复制到包含中文和空格的程序目录，BAT 原始 SHA-256 保持 `9391f5e9…e654`；数据目录也包含中文和空格。真实 BAT 启动、版本读取、正常关闭、进程退出和端口释放全部通过。
- **Windows 重复实例修复通过**：修复前两个真实 BAT/Python 实例同时存活且都报告启动成功；首实例正常关闭后，第二实例接管 8765，证明 Windows 地址复用会隐藏重复实例。`WorkbenchHTTPServer` 现仅在 Windows 禁用地址复用；第二实例返回错误码 2 和明确端口占用提示，不再虚假报告成功或接管端口，也不终止已有实例。新增 `tests/test_duplicate_instance.py` 覆盖真实端口冲突和 `main()` 错误路径；云主机 Linux 为 178 项、176 通过、2 项环境跳过、0 失败、0 错误（12.553 秒），Windows Python 3.13 为 178 项、172 通过、4 跳过、0 失败、0 错误（23.706 秒），便携 3.12.10 为 178 项、172 通过、4 跳过、0 失败、0 错误（24.137 秒）。
- **GUI 仍未验收**：桌面控制连续超时，Browser Use 随后发生 window binding timeout；本轮没有取得正常浏览器点击、布局或关闭页面证据，不能把 HTTP/API 通过描述为浏览器界面验收通过。

## 尚未完成

- 未创建 PR、Release 或正式版本，生产 `main` 未修改；V1.0 门禁完成前不得合并或发布。
- 用户真实 Windows 正常浏览器完整交互、重启、长时间运行、杀毒软件和网络权限验收未完成。
- 用户真实 SQLite、参考图片、配置的备份/原位更新/恢复和真实大库性能测量未完成。
- 全应用键盘/读屏审查、真实案例检索指标、真实视觉检索边界和真实模型/图片生成评估未完成。
- 截图中的多模型/Agent 组合尚未接入；当前仍是单个 OpenAI 兼容分析入口，不是多模型路由器，也没有 Seedream 图片生成/编辑适配器。

## 下一步

先完成用户实机与真实资料门禁，再考虑 V1.0 发布。模型接入按 `docs/MODEL_CONFIGURATION_AND_AGENT_ROADMAP.md` 的阶段 A 开始：届时只集中引导用户配置一个视觉分析提供商的真实模型 ID、HTTPS API 地址、本人 API Key 和少量测试素材，不要求同时配置全部截图模型。
