# 2027 關西行程網站

把 Obsidian 的行程筆記變成一個圖文並茂的網頁。**Obsidian 筆記是唯一資料來源**，網站只是輸出。

- 筆記：`~/Library/CloudStorage/SynologyDrive-HermesAgent/Obsidian/Travel/2027關西_5天4夜_完整版.md`
- 網站：`~/Sites/kansai-2027/index.html`（所有圖片在 `assets/img/`）

## 日常更新流程

改完 Obsidian 筆記後，任選一種：

1. **雙擊 `同步網站.command`** — 重建一次後自動開啟網頁。
2. **終端機**：`cd ~/Sites/kansai-2027 && python3 build_site.py`
3. **常駐監看**（邊改邊看）：`python3 build_site.py --watch`
   筆記一存檔，2 秒內自動重建；重新整理瀏覽器即可看到。

本機預覽：雙擊 `啟動預覽.command`，會開 `http://localhost:8787`。

## 撰寫規則（parser 依賴的格式）

腳本用固定標題抓資料，請保持這些寫法：

- 每日標題：`### Day 1｜2027-02-02（二）行程標題`
- 行程表：四欄 `| 時間 | 交通方式 | 行程 | 備註 |`
- 兩案並排：`**Plan A：方案名稱**`、`**Plan B：方案名稱**`，各自接一張四欄表
- 用餐：`- **Day 1 午餐｜區域**：**店名**（細節）[官方](url)；**店名2**（細節）[連結](url)`
- 行前清單：`## 7. 行前重查清單` 下的 `- [ ]`
- 待確認：`## 8. 待確認` 下的 `- [x]` / `- [ ]`
- 日語對照：`## 附錄：常用日語對照` 的兩欄表

標題層級或欄位順序改了，parser 會抓不到；改格式時同步改 `build_site.py`。

## 網站功能

- 首屏大圖、倒數天數、四張摘要卡
- 每日一區：大圖組、時間軸、Plan A/B 切換、用餐候選（含官方連結）
- 交通票價表、門票卡、行前清單（勾選存在瀏覽器 localStorage）、待確認、日語對照
- 手機版與列印版樣式

## 圖片

`fetch_images.py` 會從 Wikimedia Commons 抓圖並寫入 `assets/img/credits.json`（作者、授權、原始頁）。
要換圖：把同名 `.jpg` 覆蓋進 `assets/img/`，或改 `site_meta.json` 的 `days.*.gallery` 與 `captions`。
首圖固定在 CSS `.hero__img`（`assets/img/hero.jpg`）。

## 部署（已上線）

- 網址：https://richardliang103-droid.github.io/kansai-2027/
- Repo：https://github.com/richardliang103-droid/kansai-2027（public，GitHub Pages）
- 發布：雙擊 `發布網站.command`，或 `python3 build_site.py && git add -A && git commit -m update && git push`
- Pages 約 1 分鐘後更新；頁面已設 `noindex`，不會被搜尋引擎索引，但仍可用連結分享。

### 分享前的隱私處理

`site_meta.json` 的 `privacy` 區塊會在**建置時**移除／替換內容（不是只藏在畫面裡）：

- `hide_hotel_rows`：不輸出訂房金額、內部研究連結
- `replacements`：把「Agoda」改成「訂房平台」

目前公開頁面不含房價（NT$23,800）與內部筆記連結；Obsidian 原稿不受影響。

## 已知限制

- 解析依賴 Markdown 格式，不是通用 parser。
- 倒數以裝置時間計算，出遊後會顯示「—」。
- 勾選狀態只存在單一瀏覽器，不會同步回 Obsidian。
- 圖片為 Wikimedia Commons 的公開授權照片，與實際 2027 現場樣貌可能有落差。


## 視覺系統（KANSAI RAIL 2027）

方向由 agy（`gemini-4-hydrogen-high`）提出，採用日本車站導視／時刻表語彙，刻意避開暖米色＋襯線大標＋圓角霧面卡的 AI 編輯風。

- 色彩：底 `#F1F2EE`、墨 `#101418`、主色 `#005BAC`；每日一線色 D1 `#D91E2A`、D2 `#E85D04`、D3 `#0072BC`、D4 `#6F2C91`、D5 `#00875A`
- 字體：Archivo Black（拉丁大標）、Noto Sans TC（中文）、IBM Plex Sans（內文）、IBM Plex Mono（時刻／票價）
- 規則：0 圓角、1px 實線框、無模糊陰影；區塊以 4px 路線色帶識別
- 元件：首屏＝出發看板＋票券卡；每日＝月台牌頭（D1–D5）；時間軸＝單線軌道＋空心圓點；用餐＝票根卡；交通＝時刻表（大人／小孩分欄）
- 改色只改 `site_meta.json` 的 `day_colors` 與 CSS `:root`

