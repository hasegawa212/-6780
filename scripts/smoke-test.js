'use strict';

/**
 * Slackに接続せずに契約書生成部分だけを検証するスモークテスト。
 *   node scripts/smoke-test.js
 * 成功すると output/ にダミーデータの docx と pdf が出力される。
 */

require('dotenv').config();
const { generateContract } = require('../generate-contract');

const sample = {
  kaigo: '1',
  total_amount: '100,000,000',
  unit_amount: '10,000,000',
  interest_rate: '2.5',
  interest_pay_date: '毎年6月末日及び12月末日',
  maturity_date: '2029年3月31日',
  redemption_method: '満期一括償還',
  issuer_name: '株式会社サンプル',
  issuer_address: '東京都千代田区丸の内1-1-1',
  issuer_rep: '代表取締役 山田太郎',
  investor_name: '△△銀行株式会社',
  investor_address: '東京都中央区日本橋2-2-2',
  investor_rep: '代表取締役 佐藤花子',
  bank_name: 'みずほ銀行',
  branch_name: '丸の内支店',
  account_type: '普通',
  account_number: '1234567',
  payment_date: '2026年5月1日',
  issue_date: '2026年5月1日',
  contract_date: '令和8年5月1日',
  collateral: '',
};

(async () => {
  try {
    const { docxPath, pdfPath } = await generateContract(sample);
    console.log('✓ docx 生成:', docxPath);
    console.log('✓ pdf 生成:', pdfPath);
  } catch (e) {
    console.error('✗ 生成失敗:', e.message);
    process.exit(1);
  }
})();
