# 参与贡献

任何人都可以 fork 本仓库、创建功能分支并向 `main` 提交 Pull Request。所有代码必须经过 PR；禁止直接推送、强制覆盖或删除 `main`，管理员也受保护规则约束。

1. Fork：[super-guga](https://github.com/jscjscjscjscjsc/super-guga)。
2. 从最新 `main` 创建功能分支，例如 `feat/my-feature`。
3. 保持 API Key、`ai_config.json`、录音、截图和 `runtime/` 运行数据不进入提交。
4. 说明复现步骤、实际验证结果和仍存在的限制。
5. 提交 Pull Request，等待 `Desktop regression tests` 和 `Plugin contract tests` 两项 GitHub Actions 检查通过。
6. 至少一名其他协作者批准，解决审查对话，并保持分支与最新 `main` 同步后，才能合并。新提交会使旧批准失效，需重新审查。

## 桌面版与网页实验

`main` 保留咕嘎桌面应用。网页宠物、浏览器展示或网页移植在独立功能分支开发，通过 PR 说明新增入口和兼容范围；不能用网页版本替换桌面启动器、桌面界面或桌面资源。GitHub Pages 展示不代表桌面代码已合并，也不能用 Pages 部署成功代替代码测试通过。

## 自动检查范围

工作流见 `.github/workflows/ci.yml`。每个 PR 和分支推送都会检查，两个任务的名称是 `main` 的必需检查名称，不要随意更名或加路径过滤导致检查不运行。

- Windows / Python 3.12：安装应用依赖、检查依赖、编译 Python、运行独立聊天、分层记忆、每日计划、桌面窗口、手动录音、长语音稳定性与工具接口的离线回归。
- Node.js 22：检查原生 DSH 插件语法及会话事件契约。

检查不用真实 API Key、用户聊天记忆、麦克风或扬声器；测试通过不代表真实网页划词和物理设备已经验收。提交者仍须在 PR 中说明适用的真机验收结果。

本项目采用 MIT License。不要提交未经授权的歌曲、艺人录音、私人视频、第三方模型或来源不明的素材。
