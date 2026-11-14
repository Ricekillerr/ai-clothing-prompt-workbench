# 项目记忆：对话交接

最后更新：2026-11-14

## 当前项目

Prompt Atelier V0.6.5 候选版。既有项目继续完善，不是重新制作的独立演示页面。

## 本轮已完成

1. 修复组合器 Node 测试夹具，保留完整分页与跨页选择断言。
2. 修复 Windows Node 临时文件、AI 字节夹具和 SQLite 显式关闭。
3. 中文 BAT 固定 UTF-8 无 BOM + CRLF，新增 `.gitattributes`。
4. manifest 生成器保留 BAT 原始哈希、notes、发布集合与顺序。
5. 新增 `PROMPT_ATELIER_NO_BROWSER=1`；正常用户仍默认打开浏览器。
6. 双环境完整测试均 0 失败、0 错误。
7. 更新验证报告、项目记忆、V1.0 验收矩阵和模型配置路线。
8. 用户明确确认提交并推送开发分支。
9. Windows 创建两个本地提交：`aabb5d7018f40d13e923152e8ec77d4b8abba85d`、`f38c98cf7c1f7f89b2ab0e6dc703a0af1467c0f8`。
10. 两个提交已备份为 bundle，双环境校验通过，SHA-256 为 `b3e470580e8387eb1a3bcefbdf360fcf6d53399ec1f54273c839020e78239191`。
11. Windows 原生 Git 多次网络失败；禁用凭据交互后最后一次普通 push 明确连接失败，退出码 128。
12. GitHub OAuth 已实际验证成功，账号 `Ricekillerr`。
13. 代码/测试三组已通过连接器推送：`d5a3858963bc62570610815af2f8d2f75c45f3a4`、`662c54841272b25998e6dccda8c7f2848007513c`、`cea9ef245a5816f6edbc88b489a0cdd04d5a61d0`。

## 测试结果

- Linux：174 项，173 通过、1 项 Windows BAT 环境跳过，12.573 秒，OK。
- Windows：174 项，170 通过、4 项符号链接权限跳过，23.906 秒，OK。
- Windows 专项：8 项通过，6.792 秒。
- 结束后 Prompt Atelier 进程数 0，8765 监听数 0。

## 关键边界

- 用户 SQLite、参考图片、API Key、密码和个人配置不得提交 GitHub，也不得被更新覆盖。
- 80% 是检索筛选门槛，不是识别准确率或生成成功率。
- AI 分析必须人工审核后入库。
- 模型截图组合是后续规划，不是当前已接入能力。
- 当前分析器仍是单个 OpenAI 兼容 `/chat/completions` 入口。

## 下一步

1. 回读第四组文档提交和关键文件，运行并读取修复后的完整 GitHub Actions。
2. 记录本地原始两个提交与远端连接器提交的 SHA 差异；保留本地 `ahead 2` 和 bundle 作为原始备份。
3. 用户真实 Windows 浏览器与资料库验收。
4. 真实模型接入和效果评估；需要账号、API Key 或模型 ID 时再集中引导用户。
5. V1.0 全部门禁完成前，不合并 main、不发布正式版。

## 重要未决状态

- 本地原始两个提交尚未以原 SHA 出现在远端；代码/测试三组已由连接器创建不同 SHA 的等价提交，第四组文档待回读。
- 连接器推送不能直接传输本地 Git 对象；本地两个提交和 bundle 是原始哈希的权威备份。
- 修复后的完整 GitHub Actions 最终结果尚未读取，不能称“完整 CI 通过”。
- `main` 未修改，未创建 PR、Release 或正式版本。

## GitHub 基线

- 仓库：`https://github.com/Ricekillerr/ai-clothing-prompt-workbench`
- 开发分支：`release/v0.6.5-stabilization`
- 远端最初基线：`5bc19db3edb32f30cc9c834862d01da306a59fb6`
- 历史最近完整成功 CI：`https://github.com/Ricekillerr/ai-clothing-prompt-workbench/actions/runs/43546001379`
- 历史失败 CI：`https://github.com/Ricekillerr/ai-clothing-prompt-workbench/actions/runs/43547093573`
- GitHub OAuth 已实际验证成功，账号 `Ricekillerr`。
- Windows 原生 Git 网络阻断；连接器按文件创建等价远端提交，远端 SHA 与本地两个提交不同。
