# 自動転送ログ

作成日: 2026/06/03
処理スクリプト: Claude Code (Slack→Google Drive 自動転送)

## サマリ

- 処理対象（unique fid）: 84件
- 自動転送成功: 6件
- 手動転送送り: 78件
- 失敗: 0件（試行したものはすべて成功）

## 成功した転送

| # | Slack File ID | サイズ | Drive新File ID | Drive新ファイル名 |
|---|---------------|--------|---------------|--------------------|
| 1 | `F0AFD9L6Z5X` | 7.1KB | `1Ac3S_3erDdqt3Dic8Q_7wK0YKc3qWykL` | `【Slack取得】入金_平松史也_平松様源泉徴収票_FL6Z5X.pdf` |
| 2 | `F0AS2RTDGAF` | 9.9KB | `1fvIHQIDHCpH3kaN7n5_R_XoY4bF2ji1E` | `【Slack取得】入金_宮下直人_マーシャルアーツ振込完了通知宮下様3月4月分_FTDGAF.docx` |
| 3 | `F0B2XBYJDM2` | 10.3KB | `1wclGP9vY7DPT3cp1rBgRdt5vLtpDYrip` | `【Slack取得】入金_東洋建設ホーム_請求書東洋建設ホーム→マーシャルアーツ20260509_FYJDM2.docx` |
| 4 | `F093K6HC64S` | 20.2KB | `1SFLCo_wCNJmxC_PHXtjv6cfTBUAM_hfg` | `【Slack取得】入金_綱川大悟_R5年度源泉徴収票綱川様_FHC64S.pdf` |
| 5 | `F093L7N5Z5J` | 20.2KB | `1kfCgTIwQ9Y75BN9RL7TKFDrnw0udBa6i` | `【Slack取得】入金_綱川大悟_綱川様R6年度源泉徴収票_FN5Z5J.pdf` |
| 6 | `F0934UTQRBR` | 20.4KB | `1cYzf-p84e-glxh6-N2-qlO4fLHiFhXuR` | `【Slack取得】入金_綱川大悟_綱川様R4年度源泉徴収票_FTQRBR.pdf` |

## 失敗した転送

該当なし（試行した 6 件はすべて成功）

## 未試行ファイル

残り **78件** は `manual_transfer_guide.md` にまとめました。手動転送をお願いします。

### 配置先（共通）

- テストフォルダ: `1WB2gXvpgzjiI8kXzR2pZb0DRiVUQw9iK`
- URL: https://drive.google.com/drive/folders/1WB2gXvpgzjiI8kXzR2pZb0DRiVUQw9iK

## 備考

- 全 85件中 84 unique fid（F0ABT8N673N が重複登録のため）。
- 自動転送は MCP `create_file` ツール経由で実施。
- リネーム規則: `【Slack取得】{入金|出金}_{取引先}_{元ファイル名（拡張子なし）}_F{Slack File ID後半5桁}.{拡張子}`