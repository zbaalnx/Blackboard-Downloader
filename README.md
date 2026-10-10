# Blackboard Downloader · Blackboard 课程材料批量下载器

**中文** | [English](#english)

---

## 中文说明

批量下载 Blackboard **Ultra** 里你本人课程的全部材料(附件、文档页面里嵌入的文件、学习模块里的多层子文件夹),并保持原有的文件夹结构。

### 选哪个版本?

| 版本 | 文件 | 适用对象 |
|---|---|---|
| 兰开夏大学专用版 | `UCLan/blackboard_downloader_uclan.py` | 兰开夏大学 (UCLan),网址已内置,开箱即用 |
| 通用版 | `Generic/blackboard_downloader_generic.py` | 其他使用 Blackboard Ultra 的大学,首次运行时输入学校网址 |

### 功能

- **增量下载**:`_manifest.jsonl` 记录已下载的文件,之后只下载新增的文件;被误删的文件会自动补回,被你移到课程文件夹其他位置的文件不会重复下载。
- **大文件流式下载**:边下载边写盘,先写 `.part` 临时文件,完整后再改名,不占内存。
- **纯文字页面另存为 PDF**:没有文件可下载的文档页面,按"打印样式"导出为 PDF,文件名为页面标题。
- **SharePoint 文件链接自动下载**:例如课程里指向 SharePoint 的 module descriptor,会使用浏览器里已有的微软登录状态下载。
- **外部链接**记录在各文件夹下的 `_links.txt`。
- 单个条目或课程出错不会中断整个任务,日志保存在保存目录下的 `download_log.txt`。

### 安装

需要 Python 3.9 或更高版本,以及 Windows 自带的 Edge 浏览器(没有 Edge 时自动改用 Chromium)。

```bash
pip install -r requirements.txt
playwright install chromium
```

### 使用

**兰开夏大学版:**

```bash
python UCLan/blackboard_downloader_uclan.py
python UCLan/blackboard_downloader_uclan.py EL3425          # 只下载名称/代码含 EL3425 的课程
python UCLan/blackboard_downloader_uclan.py --root "E:\Study"
```

**通用版:**

```bash
python Generic/blackboard_downloader_generic.py --base-url https://blackboard.你的学校.edu
python Generic/blackboard_downloader_generic.py             # 之后会记住网址, 不用再输入
```

运行后:

1. 脚本会打开浏览器,请**手动登录** Blackboard(含 MFA)。
2. 看到 Blackboard 页面(不是登录页)后,回到命令行**按回车**。
3. 脚本自动下载,完成后会打印统计信息。

### 参数

| 参数 | 说明 |
|---|---|
| `关键字` | 只处理名称或课程代码含该关键字的课程 |
| `--root 目录` | 保存目录。默认 `~/Blackboard_Downloads`,也可用环境变量 `BB_DOWNLOAD_ROOT` |
| `--base-url 网址` | (仅通用版)学校 Blackboard 网址,也可用环境变量 `BB_BASE_URL` |
| `--redownload` | 忽略下载名单,全部重新下载 |
| `--print-all` | 所有文档页面都另存为 PDF(默认只处理没有文件的页面) |
| `--no-print` | 不生成 PDF |

### 保存位置与结构

```
<保存目录>/
├── <课程名>/<与 Blackboard 一致的文件夹结构>/文件
├── _manifest.jsonl        下载名单
├── download_log.txt       日志
└── _browser_profile/      浏览器登录状态(敏感!)
```

### 常见问题

- **提示未登录成功**:请确认浏览器里已显示 Blackboard 页面再按回车。
- **没有获取到任何课程 / 接口读取失败**:部分学校关闭了 Blackboard 的公开 REST 接口,或你的课程使用的是旧版(Original)界面,本工具只支持 Ultra 且依赖该接口。
- **文件名或路径过长**:程序已限制单个名称长度;建议把保存目录设在较短的路径下。
- **SharePoint 文件下载失败**:可能需要重新登录微软账号,或你没有该文件的访问权限;链接仍会保存在 `_links.txt`。
- **想全部重来**:加 `--redownload`。

### 隐私与安全

- 所有数据只在你自己的电脑和学校服务器之间传输,脚本不会把任何内容发送给第三方。
- **`_browser_profile/` 保存着你的登录状态,等同于账号凭证,请勿上传、分享或提交到 Git 仓库。** 分享日志或反馈问题前,请先删除其中的个人信息。
- 本工具仅用于下载**你本人有权访问**的课程材料,请遵守学校的使用条款与课程材料的版权要求。使用本工具产生的后果由使用者自行承担。

### 许可证

本项目以 [MIT 许可证](LICENSE) 开源,© 2026 Nanxi Li。

### 问题反馈

请到 GitHub 提交 Issue:**https://github.com/zbaalnx/Blackboard-Downloader/issues**(点 **New issue**)。
反馈时请附上:使用的版本、Python 版本、报错日志(**请先删除个人信息**,例如用户名、学号、课程名)。涉及安全问题请使用仓库 **Security** 页的 *Report a vulnerability* 私下报告。

---

<a id="english"></a>

## English

Bulk-downloads all materials from your own courses in Blackboard **Ultra** — attachments, files embedded in document pages, and deeply nested folders / learning modules — keeping the original folder structure.

### Which version?

| Edition | File | For |
|---|---|---|
| UCLan edition | `UCLan/blackboard_downloader_uclan.py` | University of Central Lancashire; URL built in, works out of the box |
| Generic edition | `Generic/blackboard_downloader_generic.py` | Any other university running Blackboard Ultra; you provide the URL on first run |

### Features

- **Incremental**: `_manifest.jsonl` records what has been downloaded; later runs fetch only new files. Accidentally deleted files are restored; files you moved elsewhere inside the course folder are not downloaded again.
- **Streaming for large files**: written to a `.part` file on disk and renamed when complete — no memory blow-up.
- **Text-only pages saved as PDF**: document pages with no downloadable file are exported using the print stylesheet, named after the page title.
- **SharePoint file links**: e.g. a module descriptor hosted on SharePoint is downloaded using the Microsoft sign-in already present in the browser.
- **External links** are listed in `_links.txt` in each folder.
- A failure in one item or course does not stop the run; a log is kept in `download_log.txt`.

### Installation

Requires Python 3.9+ and Microsoft Edge on Windows (falls back to Playwright Chromium if Edge is unavailable).

```bash
pip install -r requirements.txt
playwright install chromium
```

### Usage

**UCLan edition:**

```bash
python UCLan/blackboard_downloader_uclan.py
python UCLan/blackboard_downloader_uclan.py EL3425          # only courses whose name/code contains EL3425
python UCLan/blackboard_downloader_uclan.py --root "E:\Study"
```

**Generic edition:**

```bash
python Generic/blackboard_downloader_generic.py --base-url https://blackboard.your-university.edu
python Generic/blackboard_downloader_generic.py             # the URL is remembered afterwards
```

Then:

1. A browser window opens — **log in manually** (including MFA).
2. When you see the Blackboard page (not the login page), return to the terminal and **press Enter**.
3. The script downloads everything and prints a summary.

### Options

| Option | Description |
|---|---|
| `keyword` | Only process courses whose name or code contains the keyword |
| `--root DIR` | Output folder. Default `~/Blackboard_Downloads`, or env var `BB_DOWNLOAD_ROOT` |
| `--base-url URL` | (Generic edition only) your Blackboard URL, or env var `BB_BASE_URL` |
| `--redownload` | Ignore the manifest and download everything again |
| `--print-all` | Save every document page as PDF (default: only pages without files) |
| `--no-print` | Do not generate PDFs |

### Output layout

```
<output folder>/
├── <course name>/<same folder structure as Blackboard>/files
├── _manifest.jsonl        download manifest
├── download_log.txt       log
└── _browser_profile/      browser login state (SENSITIVE!)
```

### Troubleshooting

- **"Login not detected"**: make sure the browser shows the Blackboard page before pressing Enter.
- **No courses found / API errors**: some institutions disable Blackboard's public REST API, or your courses use the Original (non-Ultra) experience. This tool supports Ultra only and depends on that API.
- **Long file names / paths**: name lengths are capped; choose a short output folder path.
- **SharePoint download failed**: you may need to sign in to Microsoft again, or lack access to the file. The link is still saved in `_links.txt`.
- **Start over**: use `--redownload`.

### Privacy & safety

- Data only travels between your computer and your university's servers; nothing is sent to any third party.
- **`_browser_profile/` holds your login state and is equivalent to an account credential — never upload, share or commit it.** Remove personal information from logs before sharing them.
- Use this tool only to download materials **you are entitled to access**, and respect your institution's terms of use and copyright rules. You use it at your own risk.

### License

Released under the [MIT License](LICENSE). © 2026 Nanxi Li.

### Feedback

Please open an Issue on GitHub: **https://github.com/zbaalnx/Blackboard-Downloader/issues** (click **New issue**).
Include: edition, Python version, and the error log (**remove personal information** such as username, student ID, course names). For security problems, use *Report a vulnerability* on the repository's **Security** tab to report privately.
