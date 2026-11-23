# 视频状态说明（2026-11-19 实测口径）

> ⚠ **修订声明**：本文件 7 月版声称「V3 电影级成片 179.9s 已交付且 ffprobe 验证」，但对应 MP4 文件在当前工作树中不存在，属失实陈述，已于 2026-11-19 整体重写为实测口径。旧版内容以归档为准。

## 一、成片事实清单（三级口径，均已实测）

| # | 成片 | 位置 | 实测参数 | 状态 |
|---|------|------|----------|------|
| 1 | **V1 终版**（已随 2026-07-08 提交包 ZIP 交付 AFAC） | ZIP `QuantInsight_Pro_AFAC2026_提交包_20260708_V2.zip` 内 `submission/02_Demo交付/QuantInsight_Pro_Demo_3min.mp4`（副本：`delivery/07_视频/QuantInsight_Pro_Demo_3min_V1_终版.mp4`） | ffprobe 实测 **174.45s**，5.87 MB——7 月文档写「180s / 9.3 MB」与实测不符 | ✅ 已提交（ZIP 内可验证） |
| 2 | **最新成片**（4 分 42 秒） | `quantinsight-deploy/visualization/output/qi_demo.mp4` | ffprobe 实测 **282s**，10.8 MB，1920×1080 @30fps，烧录中文字幕；抽帧核验（30s/180s 处）均为真实运行 UI（AI 投研问答页、量化策略回测页），非摆拍 | ✅ 已产出，**待归档进提交包** |
| 3 | **≥300s 六幕新版**（目标：加入边界拒答与口径收束幕） | 目标路径 `学术加速计划/演示视频/QuantInsightPro_Demo_AFAC2026.mp4` | 渲染管线：CDP 页面级抓帧（0.15s/帧）+ edge-tts 旁白 + 程序化配乐 + ffmpeg 合成 | ⏳ **未完成**：截至 2026-11-18 共 6 次运行均失败，卡点均为登录环节（表单超时 / DOM 注入假成功 / 登录后跳转超时），待改用 session cookie 注入方案重跑 |

## 二、当前工作树实况

- `02_Demo交付/` 目录内**无任何 MP4/SRT 文件**（V3 重置时旧产物已归档清理）
- `delivery/07_视频/` 工作树目录为空（ZIP 内副本不受影响）
- 仓库内无 `.srt` 文件（7 月版 README 写「4 个 .srt」与实测不符）；最新成片字幕已烧录进画面

## 三、待办

1. **归档**：将 `visualization/output/qi_demo.mp4` 复制入 `02_Demo交付/` 与 `delivery/07_视频/`，替换/并存 V1 成片
2. **重渲染 ≥300s 版**：修复登录环节（优先方案：CDP `Network.setCookie` 注入已登录 session，绕过表单真实输入），成功后逐幕抽帧目检 + ffprobe 时长断言 ≥300s
3. 重打包提交 ZIP，使 ZIP 与工作树口径一致

## 四、实测验证方法（可复现）

```powershell
# ZIP 内 V1 成片时长
python -c "import zipfile; z=zipfile.ZipFile(r'quantinsight-deploy\submission\QuantInsight_Pro_AFAC2026_提交包_20260708_V2.zip'); print(z.getinfo('submission/02_Demo交付/QuantInsight_Pro_Demo_3min.mp4').file_size)"
# 最新成片参数
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1 "quantinsight-deploy\visualization\output\qi_demo.mp4"
# 抽帧目检
ffmpeg -y -ss 30 -i "quantinsight-deploy\visualization\output\qi_demo.mp4" -frames:v 1 check_f30.png
```
