# Windows 桌面发布与完整性级别

## 问题与触发条件

2026-10-14 的实际报错为 `新前端准备失败：[Errno 13] Permission denied: .../ai/runtime/status.json`。当时读取到的级别如下：

| 对象 | 完整性级别 |
| --- | --- |
| 工作目录 `<PROJECT_ROOT>` | Low，标签对新文件和子目录继承 |
| 新发布的 `Praat.exe` | Low，4096 / `S-1-16-4096` |
| 实际 Praat 进程 | Low，4096 |
| `ai`、`ai/runtime` 目录 | Medium，8192 |
| `ai/runtime/status.json` | Medium，8192 / `S-1-16-8192` |
| 执行发布的工具进程 | Medium，8192 |

重复触发点是**在 Low 工作目录中新建或复制桌面 EXE**。文件继承 Low 标签；Windows 创建进程时取启动方与程序文件完整性级别的较低值，Python 子进程继续沿用这个级别。Low 进程写 Medium 状态文件会被 MIC 拒绝，即使普通访问控制表 DACL 已授予当前用户修改权限。

前端 `npm run build` 生成 React/CSS/离线资源；原生构建、复制或安装阶段更新桌面 EXE。一次界面修改伴随 EXE 替换时，后一步会重新引入该问题。已经修正过的旧 EXE 标签不会自动转移到新建的文件对象。字节相同、版本相同、SHA256 相同都不能证明运行权限相同。

## 发布约束

- 最终桌面入口 `Praat.exe`、启动器、路径配置工具与本地安装引导必须是 Medium。
- 从普通用户桌面启动后的 Praat 与 Python 应为 Medium；检查进程 token，而不只看文件名或命令行。
- 文件没有显式 mandatory-label ACE 时，Windows 按 Medium 处理。检测器必须读取 `LABEL_SECURITY_INFORMATION`；不能把缺少 `icacls` 标签文字当成检测失败。
- 修改 runtime、私有配置与 IPC ACL 不属于本问题的修复步骤。保持这些文件的既有保护。
- 文件标签修正不改变已运行进程的 token。旧 Low 实例需要保存工作后退出，再从普通桌面重新启动。

## 更新流程

已有最终 EXE 经检测为 Medium、进程已退出时，可以原位写入新字节，保留该文件对象的 ACL 与标签。`本机开发/apply-model-path-menu.ps1` 使用这一分支，并核对最终标签。

新文件或已经变为 Low 的 EXE，统一使用 [Publish-NativeExecutable.ps1](../../tools/Publish-NativeExecutable.ps1)：

1. 在**同卷、Medium**目录中创建中转文件，复制程序字节，检查中转文件标签与 SHA256。
2. 备份原目标，使用同卷重命名把中转对象放到最终路径。同卷移动保留安全描述符。
3. 再次读取最终文件的标签与 SHA256；任一不符即恢复原目标并停止发布。

最终路径的 `Copy-Item` 会重新创建继承 Low 的对象，因此该步骤使用同卷移动。跨卷复制不满足此约束。

`icacls /setintegritylevel Medium` 可以直接修正有权限的文件，但修改标签要求 `WRITE_OWNER`。本次调用返回 Access denied：工具进程本身是 Medium，仍缺少该文件所需的标签修改权限。因此发布流程使用新建正确标签的同卷对象，不扩大目录权限，也不把修正失败降为警告后继续交付。

示例（PowerShell，从项目根目录执行）：

```powershell
./tools/Publish-NativeExecutable.ps1 -Source ./scratch/python-bridge-fix/Praat.exe `
  -Destination (Join-Path (Get-Location).Path 'Praat.exe') -MediumDirectory (Split-Path -Parent (Get-Location).Path)
```

## 构建与代码入口

| 入口 | 责任 |
| --- | --- |
| [NativeIntegrity.ps1](../../tools/NativeIntegrity.ps1) | 读取文件 mandatory label 和进程 token |
| [Makefile](../../Makefile) | Windows 原生链接完成后，发布最终 `Praat.exe`；其他平台不走此步骤 |
| [build-python-bridge-fix.ps1](../../tools/build-python-bridge-fix.ps1) | 原生桥接修复编译后发布 Medium 候选 |
| [build-model-path-menu.ps1](../../本机开发/build-model-path-menu.ps1) | 菜单构建调用统一发布流程，失败停止 |
| [installer/build.ps1](../../installer/build.ps1) | 编译后检查并发布启动器、路径工具和本地安装引导 |
| [desktop_launch.py](../../ai/praat_ai/desktop_launch.py) | 状态记录写入边界；代码注释解释进程级别要求 |
| [praat_python.cpp](../../sys/praat_python.cpp) | Python 子进程创建边界；代码注释解释级别继承 |

历史 `installer/verification/build-*.ps1` 仅为既有验收脚本。手工使用时，最终桌面 EXE 仍须通过上述发布步骤；其中一次成功的 relabel 不代替最终复制后的检查。

## 验收

使用相同程序字节，分别启动 Low 和 Medium 文件对象，并将隔离 AI runtime **目录及其 `status.json` 都设为 Medium**，与故障现场一致。仅在 Low 目录中放置 Medium 文件不足以复现：目录允许删除子项时，原子替换可能成功。实际编辑器“启动前端”菜单启动 Python：Low 用例应复现权限错误，Medium 用例应完成前端准备并写入状态，随后启动的前端进程也应为 Medium。记录文件与父子进程级别、退出码和状态结果；测试配置不发送模型请求。

2026-10-14 对照验证已通过：相同 SHA256 `aa39d61c…888af`，Low 的 Praat/Python 均为 4096，菜单准备返回 1 并复现 `Permission denied: .../status.json`；Medium 的 Praat/Python/前端子进程均为 8192，菜单准备返回 0，状态为 `ready-to-launch`。前端子进程使用隔离探针替代真实聊天窗口，不发送模型请求。

记录入口：[native-integrity-startup.json](../../test-records/pronunciation/native-integrity-startup.json)，测试代码：[verify_native_integrity_startup.py](../../ai/tests/verify_native_integrity_startup.py)。原生桥接构建与安装包构建也已执行；最终入口标签和哈希见 [native-integrity-publication.json](../../test-records/pronunciation/native-integrity-publication.json)。发布必须核对最终路径标签，避免只测中转候选。

Windows PowerShell 发布入口已验证 Low → Medium 的实际替换与 Medium 同字节文件的重复调用；Windows `make` 新增命令通过 dry-run 检查。安装器合同测试为 21 通过、0 失败，缺失依赖用例使用普通 Medium 临时目录中的干净 Python 环境，避免 Low 测试解释器引入同类权限错误。

## Windows 机制依据

- [Mandatory Integrity Control](https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control)：MIC 在 DACL 之前检查；Low 禁止向 Medium 写入；无标签对象按 Medium；进程创建取较低级别。
- [SECURITY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information)：读取标签需要 `READ_CONTROL`，设置标签需要 `WRITE_OWNER`。
- [File Security and Access Rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights)：新建对象获得默认安全描述符，重命名或移动不会重新赋予默认描述符。
- [MoveFile](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefile)：跨卷文件移动不保留安全描述符。
