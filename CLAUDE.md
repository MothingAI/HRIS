# 实施与协作入口

与Codex通过根目录BLACKBOARD.md协作。当前功能验收已完成，本轮文档整理由Codex按用户授权直接执行。

## 阅读顺序

1. AGENTS.md
2. BLACKBOARD.md
3. docs/START-HERE.md
4. docs/design/ARCHITECTURE.md
5. docs/operations/REPRODUCE.md
6. docs/quality/REVIEW-CHECKLIST.md

## 工作约定

- 先检查现有文件和工具版本，保留用户及其他协作者的修改。
- 重大路线变化先说明原因、替代与影响，加入黑板；普通可逆选择自行处理。
- Windows优先PowerShell，脚本以仓库根目录为工作目录。
- 不依赖额外agent或私有skill，仓库文档应足以运行。
- 真实密钥保留本地.env；新增变量同步.env.example。
- 插件dist/server是手写源码，不得按构建产物删除。
- 测试记录实际命令、目标、结果与未执行项，UI不能代替后端权限验证。
- 完成时更新BLACKBOARD为READY_FOR_REVIEW，不替Codex标记ACCEPTED。
- Git提交和推送遵循用户当前指令，提交前展示目录与README。
