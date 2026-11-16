# Prompt Atelier 轻量审查门禁验证记录（2026-11-16）

## 范围

本轮按用户确认的轻量方案整合：只读路径审查、两轮返修规则、按路径触发条件安全回归、禁止自动合并。新增：

- `tools/review_gate.py`
- `tests/test_review_gate.py`
- `.github/workflows/review-gate.yml`
- `.github/pull_request_template.md`
- `docs/AGENTIC_REVIEW_POLICY.md`
- 本验证记录及项目记忆更新

未修改九个发布运行文件、`update_manifest.json`、数据库、参考图片、AI 配置或业务数据；未安装 `gh aw`，未接入自动构建者、模型调用、PR 自动评论、自动推送、自动合并或 Release。

## 自动化验证

### 专项与条件安全回归

- `tests.test_review_gate`：9 项通过，覆盖文档路径跳过、七类信任边界、私有数据库/参考图/配置/凭据路径拦截、普通测试夹具不误报、绝对路径与越界路径拒绝、两轮上限、只读权限及 CLI 输出。
- 条件安全回归使用与工作流一致的模块列表：129 项通过，1 项 Windows 环境跳过，0 失败、0 错误，10.796 秒。
- `.github/workflows/review-gate.yml` 已由 YAML 解析器成功读取，jobs 为 `review-context` 与 `security-regression`。

### 云主机 Linux 全量

- Python 3.12。
- 190 项，188 通过、2 项 Windows 环境跳过，0 失败、0 错误，11.656 秒。
- 日志：`/home/user/Doubao/chats/38453857339133954/review-gate-full-190.log`。

### 用户 Windows 全量

- Windows Python 3.13.18，工作目录 `D:\豆-提示词工作台`。
- 190 项，186 通过、4 项权限/环境跳过，0 失败、0 错误，25.369 秒。
- 日志：`C:\Users\PC\Doubao\chats\2026-11-16\new-chat-2\review-gate-windows-full-190.log`。

## 门禁边界

- 当前仅由 PR 模板和维护者执行返修轮次记录；Runner 尚未自动读取或统计 PR 评论/审查轮次，不宣称已实现文章中的完整硬循环控制器。
- 路径检查会阻止疑似私有资料进入变更，但仓库未启用 GitHub Advanced Security，不能据此宣称内容级秘密扫描通过。
- 新工作流只有 `contents: read`，两个 job 均为 10 分钟超时；条件安全回归只在触及信任边界时运行。
- 本轮没有正常浏览器 GUI、真实 SQLite/参考图片、真实模型或长时间 idle 新证据；V1.0 相应门禁仍未完成。
- 当前为本地验证完成、待开发分支同步与远端 CI 核对状态；推送后以最终分支头和 Actions 为准。
