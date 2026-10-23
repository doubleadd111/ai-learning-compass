# 第三方依赖与参考项目

学习罗盘 2026.10 桌面版独立实现。交互设计参考 Cherry Studio、Super Productivity 和 Planify，未复制这些项目的代码或图标。

项目原创部分采用 `GPL-3.0-only`，见[LICENSE](LICENSE)和[COPYRIGHT](COPYRIGHT)。这不改变下列第三方组件的许可证或版权归属；分发时仍须保留其声明并遵守相应条款。

| 依赖 | 用途 | 许可证／项目 |
| --- | --- | --- |
| Python | 运行环境 | PSF License，https://www.python.org/ |
| PySide6 / Qt / Shiboken6 | 桌面界面与系统托盘 | LGPL-3.0 等，https://doc.qt.io/qtforpython-6/licenses.html |
| OpenAI Python SDK | 用户配置的模型服务客户端 | Apache-2.0，https://github.com/openai/openai-python |
| Pydantic / pydantic-core | 数据校验 | MIT，https://github.com/pydantic/pydantic |
| keyring | Windows 系统凭据存储 | MIT，https://github.com/jaraco/keyring |
| platformdirs | 用户数据目录 | MIT，https://github.com/tox-dev/platformdirs |
| httpx / httpcore | HTTPS 请求 | BSD-3-Clause，https://github.com/encode/httpx |
| certifi | CA 证书 | MPL-2.0，https://github.com/certifi/python-certifi |

安装包同时提供 `licenses` 目录，保存构建时依赖发行包携带的许可证，含 Qt 所依赖的组件声明。Qt / PySide6 使用独立动态库分发，可在解压即用目录中替换兼容的库；未将这些动态库静态链接进程序。依赖对应源码可在表中项目及 https://download.qt.io/official_releases/QtForPython/ 获取。重建方式见 `docs/desktop-guide.md`。

PyInstaller 和 Inno Setup 用于构建，不作为独立开发工具安装到用户电脑；生成的可执行程序包含相应引导或安装代码。各自许可证见 https://pyinstaller.org/ 与 https://jrsoftware.org/ 。PyInstaller生成程序的许可适用其分发例外，不能据此忽略应用依赖本身的许可义务。

源码中的网页模式另使用Streamlit（Apache-2.0）和psycopg（LGPL-3.0）；python-dotenv为BSD-3-Clause。网页依赖不等同于桌面包内组件，实际分发内容应按构建清单核对。初步核对范围与未完成事项见[许可检查](docs/licensing.md)。
