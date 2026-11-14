# 项目记忆：当前状态

最后更新：2026-11-14

## 项目定位

Prompt Atelier（AI 服装视觉资产与提示词智能生产工作台）。本地优先，HTML/CSS/JavaScript + Python + SQLite + GitHub。

当前候选版本：**0.6.5**。

## 本轮代码稳定化已完成

- 修复组合器分页 Node 测试夹具缺少 `composeNow`、`copyText`、`saveDraft` 函数桩；业务组合器未改。
- Windows Node 测试从超长 `node -e` 改为临时 `.js`，避免 `WinError 206`。
- AI/存储提示词夹具改用 `write_bytes()`，保护原文和 SHA-256。
- SQLite 测试显式关闭连接，避免 WAL/数据库文件占用。
- 中文 BAT 固定 UTF-8 无 BOM + CRLF；新增 `.gitattributes` 的 `*.bat -text`。
- 清单生成器保留 BAT 原始字节哈希、既有 notes、发布文件集合和哈希顺序。
- 新增 `PROMPT_ATELIER_NO_BROWSER=1` 无界面开关；正常用户默认浏览器行为不变。

## 最终自动化结果

### 云主机 Linux

- 174 项；173 通过、1 项 Windows BAT 环境跳过；0 失败、0 错误。
- 12.573 秒，整体 OK。

### Windows 本机

- 174 项；170 通过、4 项符号链接权限跳过；0 失败、0 错误。
- 23.906 秒，整体 OK。
- 专项 8 项全部通过，6.792 秒。
- 4 个权限跳过不算通过。

### 清理状态

- Prompt Atelier 进程数 0。
- 8765 端口监听数 0。

## 关键校验值

- BAT 原始 SHA-256：`9391f5e9afa52cc9e7b8e8c32bcea5ffc596a475061521c4ad990f911673e654`
- manifest SHA-256：`b0564732c9cad23f09f1ca1456c3d03190624974091ec696e99937e45b97361e`
- 双环境 tracked diff SHA-256：`d8e045c114678d74187bf983ae78fba4b92e7917ffe692d63eb39da4a55f701a`

## 本地提交与远端分支

仓库：`https://github.com/Ricekillerr/ai-clothing-prompt-workbench`

开发分支：`release/v0.6.5-stabilization`

远端最初基线：`5bc19db3edb32f30cc9c834862d01da306a59fb6`

本地原始提交：

- `aabb5d7018f40d13e923152e8ec77d4b8abba85d`：17 个文件，364 行新增、82 行删除。
- `f38c98cf7c1f7f89b2ab0e6dc703a0af1467c0f8`：本报告、验收矩阵及三个状态记忆文件。

本地原始提交尚未以原 SHA 出现在远端。两个提交已制成 bundle，双环境 `git bundle verify` 通过：

`b3e470580e8387eb1a3bcefbdf360fcf6d53399ec1f54273c839020e78239191`

GitHub OAuth 已实际验证成功，账号为 `Ricekillerr`。代码/测试三组已经通过连接器推送：

- `d5a3858963bc62570610815af2f8d2f75c45f3a4`
- `662c54841272b25998e6dccda8c7f2848007513c`
- `cea9ef245a5816f6edbc88b489a0cdd04d5a61d0`

本文件随第四组文档提交，最终文档提交 SHA 和完整 CI 结果待回读。连接器提交与本地两个提交 SHA 不同，不得混称同一提交。

## GitHub 与发布状态

- `main` 未修改。
- 代码/测试三组已推送；第四组文档提交和逐文件核验待完成。
- 修复后的完整 GitHub Actions 最终结果尚未读取。
- 历史最近完整成功 CI：`https://github.com/Ricekillerr/ai-clothing-prompt-workbench/actions/runs/43546001379`。
- 组合器改动后的历史失败 CI：`https://github.com/Ricekillerr/ai-clothing-prompt-workbench/actions/runs/43547093573`。
- 未创建 PR、Release 或正式版本。
- V1.0 门禁完成前不合并 main、不发布正式版。

## 本地 Git 网络

- Windows 有 Git 作者配置和 Git Credential Manager，但没有 `gh`。
- Windows 直接 push 多次被网络阻断；禁用凭据交互后的最后一次普通 push 明确为连接失败，退出码 128。
- 云主机 HTTPS 只读 `ls-remote` 成功；SSH 因主机键验证失败不可用。
- 云主机有 `gh`，此前未登录；本轮实际可用的是已授权 GitHub OAuth 连接器。
- 连接器按文件创建远端提交，不能直接传输本地 Git 对象；bundle 保留原始提交。

## 模型配置

截图组合已登记为规划，未实际接入。当前仍是单个 OpenAI 兼容分析器，默认模型字段 `gpt-4o-mini`，不是多 Agent 路由器或 Seedream 图片生成接口。

详见：`docs/MODEL_CONFIGURATION_AND_AGENT_ROADMAP.md`。

本轮不需要用户配置模型 API Key。

## 仍待完成

- 回读第四组最终提交、核对关键文件与 BAT 字节，并运行/读取修复后的完整 GitHub Actions。
- 记录本地原始提交与远端连接器提交的 SHA 差异，保留 bundle 恢复路径。
- 用户真实 Windows 正常浏览器完整交互。
- 用户真实资料库备份、原位更新和恢复。
- 杀毒软件、网络权限、重启和长时间运行。
- 真实检索指标、完整键盘/读屏审查和真实大库性能。
- 真实模型生成与人工评估。
- 所有 V1.0 门禁完成前，不合并 main、不发布正式版。
