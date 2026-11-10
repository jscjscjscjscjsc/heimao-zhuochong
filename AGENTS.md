# super 咕嘎协作规则

- 代码改动从最新 `main` 创建独立分支，通过 Pull Request 合并；不得直接推送、强制覆盖或删除 `main`，不得关闭保护以绕过测试或审查。
- 合并前 `Desktop regression tests`、`Plugin contract tests` 必须通过，并取得至少一名其他协作者的有效批准；不得代替协作者自我批准。
- 网页版与浏览器实验使用独立功能分支和新增入口，保留桌面版启动、窗口、资源与原有能力；未获批准不能用网页实现替换桌面版。
- 测试范围及贡献流程见 `CONTRIBUTING.md` 和 `.github/workflows/ci.yml`；实际设备验收与离线测试分开报告。
- 密钥、本机配置、记忆数据库、录音、截图及 `runtime/` 不进入提交。保护其他正在进行的本地改动，必要时使用隔离 worktree。
