'use strict';

const DIGITS = /^\d+$/;
const NUMBER_WITH_COMMA = /^[\d,]+$/;
const DECIMAL = /^\d+(\.\d+)?$/;

function validateStep1(v) {
  const errors = {};
  if (!DIGITS.test(v.kaigo || '')) {
    errors.b_kaigo = '回号は半角数字で入力してください（例: 1）';
  }
  if (!NUMBER_WITH_COMMA.test(v.total_amount || '')) {
    errors.b_total_amount = '数字とカンマのみで入力してください（例: 100,000,000）';
  }
  if (!NUMBER_WITH_COMMA.test(v.unit_amount || '')) {
    errors.b_unit_amount = '数字とカンマのみで入力してください（例: 10,000,000）';
  }
  if (!DECIMAL.test(v.interest_rate || '')) {
    errors.b_interest_rate = '数字で入力してください（例: 2.5）';
  } else {
    const rate = parseFloat(v.interest_rate);
    if (rate < 0 || rate > 100) {
      errors.b_interest_rate = '利率は 0〜100 の範囲で入力してください';
    }
  }
  return errors;
}

function validateStep2(v) {
  const errors = {};
  for (const [field, block, label] of [
    ['issuer_name', 'b_issuer_name', '発行者名'],
    ['issuer_address', 'b_issuer_address', '発行者住所'],
    ['issuer_rep', 'b_issuer_rep', '発行者代表者名'],
    ['investor_name', 'b_investor_name', '投資家名'],
    ['investor_address', 'b_investor_address', '投資家住所'],
    ['investor_rep', 'b_investor_rep', '投資家代表者名'],
  ]) {
    if (!(v[field] || '').trim()) {
      errors[block] = `${label}は必須です`;
    }
  }
  return errors;
}

function validateStep3(v) {
  const errors = {};
  if (!DIGITS.test(v.account_number || '')) {
    errors.b_account_number = '口座番号は半角数字で入力してください';
  }
  for (const [field, block, label] of [
    ['bank_name', 'b_bank_name', '銀行名'],
    ['branch_name', 'b_branch_name', '支店名'],
    ['payment_date', 'b_payment_date', '払込期日'],
    ['issue_date', 'b_issue_date', '発行日'],
    ['contract_date', 'b_contract_date', '契約日'],
  ]) {
    if (!(v[field] || '').trim()) {
      errors[block] = `${label}は必須です`;
    }
  }
  return errors;
}

module.exports = { validateStep1, validateStep2, validateStep3 };
