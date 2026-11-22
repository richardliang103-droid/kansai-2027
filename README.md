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

## 部署（尚未執行）

靜態檔案，可直接丟 Vercel / Netlify / Cloudflare Pages：

```bash
cd ~/Sites/kansai-2027
npx vercel deploy --prod        # 首次需登入
```

若要讓家人從手機看，建議部署；內容含訂房價格，部署前先確認是否要改成 private / 密碼保護。

## 已知限制

- 解析依賴 Markdown 格式，不是通用 parser。
- 倒數以裝置時間計算，出遊後會顯示「—」。
- 勾選狀態只存在單一瀏覽器，不會同步回 Obsidian。
- 圖片為 Wikimedia Commons 的公開授權照片，與實際 2027 現場樣貌可能有落差。
