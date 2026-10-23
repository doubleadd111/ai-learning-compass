# 许可证与分发检查

## 维护者已确认的选择

2026-10-22，维护者确认采用 `GPL-3.0-only`：允许商用和修改，保留相关版权/许可声明；分发受GPL覆盖的程序或衍生版本时，向接收者提供相应版本的对应源码，并履行GPLv3条件。私下修改使用不要求公开。仅通过网络提供功能而未交付软件副本，不自动触发GPL的源码交付义务；GPL不是AGPL。

项目许可声明位于根目录COPYRIGHT，完整GPLv3原文位于LICENSE。LICENSE末尾的通用应用示例不是本项目对未来GPL版本的授权，项目选择始终为GPL第3版only。第三方文件若有自身声明，应保留并按其许可处理，不擅自改写版权主体。

版权标识采用维护者公开账号`doubleadd111`。Git历史作者名的初步检查仅发现该账号，但提交作者不能单独证明完整权属；发布前维护者仍应确认没有未获授权的外来代码、雇主/学校权利主张或其他冲突。本次没有读取或处理无关简历、私人资料及数据库。

## 标准文本来源

- [SPDX GPL-3.0-only标准文本](https://github.com/spdx/license-list-data/blob/main/text/GPL-3.0-only.txt)，保留全文，不自行修改条款。
- 本次取得的原文SHA256：`fb981668c18a279e285fc4d83fba1e836cc84dd4daa73c9697d3cfd2d8aca6e0`。
- 对应Git blob：`f6cdd22a6c1fbc887e08a215cb4beb3c47048041`，UTF-8原文34674字节。
- [GNU对源码提供义务的说明](https://www.gnu.org/licenses/gpl-faq.en.html#GPLRequireSourcePostedPublic)。

## 2026-10-22依赖许可初步核对

本表依据本机已安装发行包的许可元数据及已有第三方声明，不等同于所有传递依赖、二进制文件或权属的完整法律审计。它也不锁定未来requirements范围内所有版本；每次分发仍须检查实际版本、文件和完整许可。

| 组件／用途 | 本次元数据或现有声明 | 处理要求 |
|---|---|---|
| Python运行环境 | PSF许可 | 保留其许可与组件声明 |
| PySide6／Essentials／Shiboken6 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | 按可用且适用的许可合规分发，不强制选择GPL2-only；Qt内部组件另行保留许可 |
| OpenAI SDK 1.109.1 | Apache-2.0 | 保留Apache声明、适用NOTICE及许可，不把依赖版权改为项目维护者 |
| Pydantic 2.13.5／pydantic-core 2.46.5 | MIT | 保留许可与版权声明 |
| keyring 25.7.0／platformdirs 4.13.0 | MIT | 保留许可与版权声明 |
| httpx 0.28.1／httpcore 1.0.9／python-dotenv 1.2.3 | BSD-3-Clause | 保留许可与版权声明 |
| certifi 2026.7.22 | MPL-2.0 | 保留其原许可、源文件及对应源码获取路径；如合并或修改源码，另核对MPL兼容条件 |
| Streamlit 1.63.0（网页） | Apache-2.0 | 保留原许可；不是桌面包的当然组成部分 |
| psycopg／psycopg-binary 3.3.6（网页） | LGPL-3.0-only | 核对实际动态链接／分发条件与上游声明 |
| PyInstaller 6.22.3 | GPL许可及应用分发例外；部分文件Apache-2.0 | 应用包按例外及依赖许可处理，不因构建工具本身GPL而误判应用许可 |
| Inno Setup | 上游自身许可 | 不改上游许可，安装和卸载代码按上游分发规则核对 |

核对参考：[Apache对GPLv3兼容性的说明](https://www.apache.org/licenses/GPL-compatibility.html)、[Qt for Python许可](https://doc.qt.io/qtforpython-6/licenses.html)、[Mozilla MPL常见问题](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)、[PyInstaller分发例外](https://pyinstaller.org/en/stable/license.html)。未发现上述元数据中存在明显要求项目放弃GPLv3的条款，但这不是对全部依赖或所有分发方式的合规保证。

## 后续发布必须检查

- 安装版和便携版都携带项目LICENSE、COPYRIGHT和第三方声明，依赖许可证目录也须保留。
- 提供二进制所对应的准确源码版本、必要构建脚本及适用依赖源码获取方式；不能只指向会变化的master分支或不同版本。
- 保留构建清单中的源码提交、依赖版本、文件摘要。新增或变更第三方组件后重新核对其完整许可和源代码提供义务。
- 不以签名、安装器、私钥或账号限制接收者按GPL获得、修改和运行软件的权利。私钥本身不随源码或安装包分发。
- 阶段47最初只作本地源码配置，2026.10.13.1起的本机测试携带新增许可文件。本轮2026.10.14公开构建同样携带LICENSE/COPYRIGHT及第三方声明，以其准确标签提供对应项目源码和构建脚本；实际提交、依赖版本和摘要见发布清单。已有2026.10.13及更早标签和五个发布附件未被改写，不能声称旧附件已包含新增文件。数据升级和功能验证另见验收记录，不等同于签名批准或完整法律审计。

## SignPath申请还未完成

项目许可选择不是签名资格批准。仍需完成真实隐私说明、签名政策、可验证的GitHub托管构建，以及维护者本人确认账号多因素认证、角色和项目权属。尚未提交申请或得到签名证书；不得提前写“已由SignPath提供签名”，也不保证审核通过。
