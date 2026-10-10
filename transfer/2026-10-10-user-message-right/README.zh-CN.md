# 用户消息右对齐

分支：`codex/chat-user-right-transfer`。
基准提交：`aaf0e31341a8f2ff961aedbe8107fe1b884768b4`。

修改新的 React / assistant-ui 工作台的 `ai/frontend/src/styles.css`：用户头像、消息气泡、附件和操作区靠右，正文保持左对齐。AI 回复保持原布局。旧 Tk 窗口没有改动。

## 在另一台电脑并入改动

在需要继续开发的分支执行，保留该电脑自己的开发进度：

```powershell
git fetch origin codex/chat-user-right-transfer
git cherry-pick FETCH_HEAD
```

这会并入本次独立提交，包括传输说明和补丁。若产生冲突，保留该电脑已有的样式修改，并加入本包的 `.message.user` 规则。

## 只使用补丁

无法拉取分支时，也可以将本目录放到目标仓库的相同位置，在仓库根目录执行：

```powershell
git apply --check transfer/2026-10-10-user-message-right/user-message-right.patch
git apply transfer/2026-10-10-user-message-right/user-message-right.patch
```

补丁只修改聊天样式。分支和补丁任选一种，不要重复应用。

## 重建并继续开发

```powershell
cd ai/frontend
npm run build
```

若尚未安装前端依赖，先执行 `npm ci`。构建会更新桌面使用的 `dist` 离线资源和随包源码副本。关闭并重新打开 AI 工作台后查看效果，无需重新编译原生 Praat。

本包提供源码，由目标电脑构建前端资源。此前同一组样式规则已通过 1280、900、390 三种宽度的长短消息及附件检查、8 项相关浏览器测试和 12 项 WebView2 桌面检查；本次另检查补丁可以应用于上述基准源码。

本机迁移时的 Low 完整性标签修复属于 Windows 文件权限元数据，不随 Git 源码传输。目标电脑沿用自己的安装目录、Python 环境和模型/API 配置。
