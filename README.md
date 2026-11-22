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

視覺系統採用 [VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md) 的 **linear.app DESIGN.md**：近黑畫布 `#010102`、四階 surface 面板、1px hairline、單一薰衣草藍 `#5E6AD2`、Inter 600 負字距大標、8/12/16px 圓角。

- 色彩：canvas `#010102`、surface-1 `#0F1011`、surface-2 `#141516`、hairline `#23252A`、ink `#F7F8F8`、accent `#5E6AD2`
- 字體：Inter 400/500/600（拉丁與數字）、Noto Sans TC、JetBrains Mono（時刻／票價）
- 規則：surface ladder 分層、1px hairline、無陰影、單一 accent；按鈕 8px、卡片 12px、面板 16px
- 元件：首屏＝display 大標＋CTA＋資訊面板；每日＝surface-1 app 面板；時間軸＝髮絲線＋藍點列表；用餐＝面板卡；交通＝資料表（大人／小孩右對齊等寬數字）
- 改色只改 `site_meta.json` 的 `day_colors` 與 CSS `:root`



## 佈景主題

- 右上角切換鈕：Light / Dark，狀態存在 `localStorage("kansai-theme")`，重整後保留
- 首次造訪跟隨系統 `prefers-color-scheme`，預設深色
- 淺色版沿用 Linear 的 inverse token（白底、`#F5F6F6` 面板、`#E6E7EB` hairline），accent 不變
- 列印一律強制白底黑字，不受當前模式影響
