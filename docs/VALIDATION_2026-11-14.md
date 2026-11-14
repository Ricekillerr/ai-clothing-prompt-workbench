# Prompt Atelier V0.6.5 双环境验证记录

验证日期：2026-11-14

## 结论

本轮在既有项目实际源码上完成稳定化修复，并在云主机 Linux 与 Windows 本机分别运行完整测试。结果均为 **0 失败、0 错误**。

- 云主机：174 项，173 通过、1 项因 Linux 不执行真实 Windows BAT 测试而跳过，12.573 秒，整体 OK。
- Windows：174 项，170 通过、4 项因符号链接权限跳过，23.906 秒，整体 OK。
- Windows 专项：真实 BAT、浏览器默认/无界面行为及 6 个 upgrade bridge 测试，共 8 项通过，6.792 秒。
- 测试结束后：Prompt Atelier 相关进程数为 0，8765 端口监听数为 0。

这些结果说明当前候选版代码在双环境自动化回归中稳定，不等同于 V1.0 全部真实用户验收已完成。

## 本轮实际修复

### 1. 组合器分页 Node 测试夹具

补充 `composeNow`、`copyText`、`saveDraft` 三个模拟函数桩，解决 `composeNow is not defined`。只修测试环境，没有跳过测试或放宽断言。

验证覆盖：

- 80 个模块按 30/30/20 分页；
- 第二页边界；
- 跨页选择保留；
- 搜索后 11 条及页码重置。

### 2. Windows Node 临时文件

`test_frontend_progress.py` 从超长 `node -e` 命令行改为临时 `.js` 文件，避免 `WinError 206`。

### 3. AI 提示词字节夹具

AI/存储测试使用 `write_bytes()` 写入提示词，避免 Windows 将 LF 转为 CRLF 后改变原文和哈希。

### 4. SQLite 显式关闭

相关测试使用 `try/finally` 关闭连接，避免 WAL/数据库句柄占用导致 Windows 临时目录清理失败。

### 5. 中文 BAT 真实启动修复

原始 BAT 为 UTF-8 无 BOM、35 个 LF、0 个 CRLF，CMD 对 LF-only 中文 BAT 解析异常。转换为 CRLF 后，服务启动、版本、心跳和关闭正常。

新增 `.gitattributes`：

```gitattributes
*.bat -text
```

最终 BAT 为 UTF-8 无 BOM、35 个 CRLF、0 个裸 LF。

BAT 原始 SHA-256：

`9391f5e9afa52cc9e7b8e8c32bcea5ffc596a475061521c4ad990f911673e654`

### 6. 更新清单生成器

修复后：

- 非 BAT 文本仍归一化换行；
- BAT 按原始字节计算 SHA-256；
- 保留既有 notes、9 个发布文件和哈希排列；
- 新文件才按当前发布列表补入；
- 连续两次生成字节一致。

最终 `update_manifest.json` SHA-256：

`b0564732c9cad23f09f1ca1456c3d03190624974091ec696e99937e45b97361e`

### 7. 自动化无界面开关

新增 `PROMPT_ATELIER_NO_BROWSER=1`，仅用于需要无界面的自动化测试。正常用户双击 BAT 时仍默认打开浏览器。

单元测试分别验证：

- 无界面模式不创建浏览器 Timer；
- 默认模式仍创建并启动 Timer。

## 双环境结果详情

### 云主机

```text
Ran 174 tests in 12.573s
OK (skipped=1)
```

跳过原因：真实 Windows BAT 测试要求 Windows `cmd.exe`。

### Windows

```text
Ran 174 tests in 23.906s
OK (skipped=4)
```

4 个跳过项均为符号链接权限测试：

- collector 符号链接；
- AI reference 重定向；
- 完整备份符号链接；
- media 符号链接防护。

权限跳过没有计为通过。

### Windows 专项

```text
Ran 8 tests in 6.792s
OK
```

覆盖真实 BAT、默认/无界面浏览器行为和 6 个 upgrade bridge 测试。

## 双环境同步核验

- Windows 与云主机 tracked diff SHA-256 一致：`d8e045c114678d74187bf983ae78fba4b92e7917ffe692d63eb39da4a55f701a`。
- 三个新增文件的 SHA-256 逐项一致。
- 最终 BAT、manifest 和同步文件重新核验。
- 未提交用户数据库、参考图片、API Key、密码或个人配置。

## 本地 Git 提交与远端等价推送

用户已明确确认提交并推送开发分支。

- 本地修复提交：`aabb5d7018f40d13e923152e8ec77d4b8abba85d`，17 个文件，364 行新增、82 行删除。
- 本地状态记录提交：`f38c98cf7c1f7f89b2ab0e6dc703a0af1467c0f8`，包含本报告、验收矩阵和三个项目记忆状态文件。
- 两个本地提交已制成 bundle，SHA-256 为 `b3e470580e8387eb1a3bcefbdf360fcf6d53399ec1f54273c839020e78239191`；Windows 与云主机均通过 `git bundle verify`。
- Windows 原生 Git 的只读查询和普通 push 多次被网络阻断；禁用凭据交互后，最后一次普通 push 明确返回 `Failed to connect to github.com:443`，退出码 128。
- GitHub OAuth 随后已实际验证成功，账号为 `Ricekillerr`。
- 为完成已授权的开发分支推送，改用 GitHub 连接器按文件创建等价远端提交；远端提交 SHA 与本地两个提交不同，不声称保留原哈希。
- 代码/测试三组已推送：`d5a3858963bc62570610815af2f8d2f75c45f3a4`、`662c54841272b25998e6dccda8c7f2848007513c`、`cea9ef245a5816f6edbc88b489a0cdd04d5a61d0`。
- 本报告随第四组文档提交；最终文档提交 SHA、逐文件一致性和修复后的完整 Actions 仍需推送后回读。
- 本地原始两个提交、工作树和 bundle 仍是原哈希的权威备份。

## GitHub 状态

- 远端最初已确认基线为 `5bc19db3edb32f30cc9c834862d01da306a59fb6`。
- 开发分支代码/测试三组已推送，第四组文档提交待最终回读；`main` 未修改。
- 修复后的完整 GitHub Actions 尚未读取最终结果，不能称“完整 CI 通过”。
- 历史最近完整成功 CI：`https://github.com/Ricekillerr/ai-clothing-prompt-workbench/actions/runs/43546001379`，只代表旧的 168 项基线。
- 未创建 PR、Release 或正式版本，也未合并 `main`。

## 仍需完成的真实门禁

- 回读第四组最终提交，核对关键文件与 BAT 字节，并运行/读取修复后的完整 GitHub Actions。
- 记录本地原始两个提交与远端连接器提交的 SHA 差异；不得把本地 `ahead 2` 误写成“同一提交已同步”。
- 用户真实 Windows 正常浏览器完整交互。
- 用户真实 SQLite、参考图片和个人配置的原位更新与恢复。
- 杀毒软件、网络权限、重启和长时间运行。
- 真实案例检索指标与完整键盘/读屏审查。
- 真实大库性能测量。
- 模型 API、真实生成效果与人工评估。

## 模型配置路线

截图中的 Kimi Code、Kimi K3.1 Pro、DeepSeek V4.1 Flash、GPT-6、DeepSeek/GLM、Seedream 5.5 等属于后续规划，不是已接入能力。

当前实际仍为单个 OpenAI 兼容 `/chat/completions` 分析入口，默认模型字段 `gpt-4o-mini`。多 Agent 路由和独立图片生成/编辑接口需要后续实现与真实验证。

本轮未要求模型 API Key。
