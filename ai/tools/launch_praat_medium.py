"""本机临时办法：把 Praat 放到工作区之外启动（绕开 Low 完整性标签）。

适用情形：仓库所在的目录被打了 **Low 完整性标签**。Windows 会按**映像文件**的标签决定新进程
的完整性级别，所以从 ``稳定早期版\\Praat.exe`` 启动的 Praat 一定是 Low；Low 进程写不了
Medium 的 ``%APPDATA%\\Praat\\Message.txt``，于是对话前端**一条指令都送不出去**
（`[Errno 13] Permission denied`，表现为「VOT 没完成」）。

这个脚本把同一份 ``Praat.exe`` 复制到 ``%TEMP%``（那里没有 Low 标签）再启动，并用
``PRAAT_AI_PROJECT_DIR`` 把原生侧的项目目录指回仓库的 ``ai``（原生侧按「工作目录 →
可执行文件目录」找 ``ai``，换了 exe 位置就找不到）。

    python ai/tools/launch_praat_medium.py [--project <仓库目录>] [--executable <Praat.exe>]

真正的修法是把标签去掉（``icacls <目录> /setintegritylevel Medium``，会让受限会话
不能再写那个目录）或者把 Praat 装在标签之外；本脚本只是让人先能用。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default=str(Path(__file__).resolve().parents[2]),
                        help='仓库目录（里面应有 Praat.exe 与 ai/）')
    parser.add_argument('--executable', default='', help='要启动的 Praat.exe（默认用项目的）')
    parser.add_argument('--copy-to', default='', help='复制到哪（默认 %%TEMP%%\\aipraat-medium-praat）')
    args = parser.parse_args()

    project = Path(args.project).resolve()
    source = Path(args.executable).resolve() if args.executable else project / 'Praat.exe'
    if not source.is_file():
        print(f'找不到 {source}')
        return 1
    target_directory = Path(args.copy_to) if args.copy_to else Path(
        os.environ.get('TEMP', str(Path.home()))) / 'aipraat-medium-praat'
    target_directory.mkdir(parents=True, exist_ok=True)
    target = target_directory / source.name
    shutil.copy2(source, target)

    environment = dict(os.environ)
    environment['PRAAT_AI_PROJECT_DIR'] = str(project / 'ai')
    process = subprocess.Popen([str(target)], cwd=str(project), env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f'已启动 {target}（pid {process.pid}），项目目录 {environment["PRAAT_AI_PROJECT_DIR"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
