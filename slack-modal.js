'use strict';

function plainText(text) {
  return { type: 'plain_text', text, emoji: true };
}

function textInput(blockId, label, actionId, opts = {}) {
  const element = {
    type: 'plain_text_input',
    action_id: actionId,
  };
  if (opts.placeholder) element.placeholder = plainText(opts.placeholder);
  if (opts.multiline) element.multiline = true;
  if (opts.initial_value) element.initial_value = opts.initial_value;

  return {
    type: 'input',
    block_id: blockId,
    optional: !!opts.optional,
    label: plainText(label),
    element,
  };
}

function selectInput(blockId, label, actionId, options) {
  return {
    type: 'input',
    block_id: blockId,
    label: plainText(label),
    element: {
      type: 'static_select',
      action_id: actionId,
      options: options.map((o) => ({ text: plainText(o), value: o })),
    },
  };
}

function sectionHeader(text) {
  return { type: 'section', text: { type: 'mrkdwn', text } };
}

function buildStep1Modal(initial = {}) {
  return {
    type: 'modal',
    callback_id: 'shibosei_step1',
    private_metadata: JSON.stringify(initial),
    title: plainText('私募債契約書 1/3'),
    submit: plainText('次へ'),
    close: plainText('キャンセル'),
    blocks: [
      sectionHeader('*ステップ1: 社債の基本情報*'),
      textInput('b_kaigo', '回号', 'kaigo', { placeholder: '例: 1' }),
      textInput('b_total_amount', '発行総額（円）', 'total_amount', { placeholder: '例: 100,000,000' }),
      textInput('b_unit_amount', '各社債の金額（円）', 'unit_amount', { placeholder: '例: 10,000,000' }),
      textInput('b_interest_rate', '利率（年率 %）', 'interest_rate', { placeholder: '例: 2.5' }),
      textInput('b_interest_pay_date', '利払日', 'interest_pay_date', {
        placeholder: '例: 毎年6月末日及び12月末日',
      }),
      textInput('b_maturity_date', '償還期限', 'maturity_date', { placeholder: '例: 2029年3月31日' }),
      selectInput('b_redemption_method', '償還方法', 'redemption_method', [
        '満期一括償還',
        '分割償還',
      ]),
    ],
  };
}

function buildStep2Modal(accumulated) {
  return {
    type: 'modal',
    callback_id: 'shibosei_step2',
    private_metadata: JSON.stringify(accumulated),
    title: plainText('私募債契約書 2/3'),
    submit: plainText('次へ'),
    close: plainText('キャンセル'),
    blocks: [
      sectionHeader('*ステップ2: 発行者・投資家情報*'),
      textInput('b_issuer_name', '発行者名', 'issuer_name', { placeholder: '例: 株式会社○○' }),
      textInput('b_issuer_address', '発行者住所', 'issuer_address', {
        placeholder: '東京都千代田区...',
        multiline: true,
      }),
      textInput('b_issuer_rep', '発行者代表者名', 'issuer_rep', {
        placeholder: '代表取締役 山田太郎',
      }),
      textInput('b_investor_name', '投資家名', 'investor_name', { placeholder: '例: △△銀行' }),
      textInput('b_investor_address', '投資家住所', 'investor_address', {
        placeholder: '東京都中央区...',
        multiline: true,
      }),
      textInput('b_investor_rep', '投資家代表者名', 'investor_rep', {
        placeholder: '代表取締役 佐藤花子',
      }),
    ],
  };
}

function buildStep3Modal(accumulated) {
  return {
    type: 'modal',
    callback_id: 'shibosei_step3',
    private_metadata: JSON.stringify(accumulated),
    title: plainText('私募債契約書 3/3'),
    submit: plainText('契約書を生成'),
    close: plainText('キャンセル'),
    blocks: [
      sectionHeader('*ステップ3: 振込先・日付・担保*'),
      textInput('b_bank_name', '銀行名', 'bank_name', { placeholder: '例: みずほ銀行' }),
      textInput('b_branch_name', '支店名', 'branch_name', { placeholder: '例: 丸の内支店' }),
      selectInput('b_account_type', '口座種類', 'account_type', ['普通', '当座']),
      textInput('b_account_number', '口座番号', 'account_number', { placeholder: '例: 1234567' }),
      textInput('b_payment_date', '払込期日', 'payment_date', { placeholder: '例: 2026年5月1日' }),
      textInput('b_issue_date', '発行日', 'issue_date', { placeholder: '例: 2026年5月1日' }),
      textInput('b_contract_date', '契約日', 'contract_date', { placeholder: '例: 令和8年5月1日' }),
      textInput('b_collateral', '担保条件（任意）', 'collateral', {
        placeholder: '無担保の場合は空欄',
        multiline: true,
        optional: true,
      }),
    ],
  };
}

function row(label, value) {
  const shown = value && String(value).trim() ? value : '（未入力）';
  return `*${label}:* ${shown}`;
}

function buildConfirmModal(d) {
  const summary = [
    '*1. 社債の基本情報*',
    row('回号', `第${d.kaigo}回`),
    row('発行総額', `${d.total_amount}円`),
    row('各社債の金額', `${d.unit_amount}円`),
    row('利率', `年${d.interest_rate}%`),
    row('利払日', d.interest_pay_date),
    row('償還期限', d.maturity_date),
    row('償還方法', d.redemption_method),
    '',
    '*2. 発行者・投資家情報*',
    row('発行者', `${d.issuer_name}（${d.issuer_rep}）`),
    row('発行者住所', d.issuer_address),
    row('投資家', `${d.investor_name}（${d.investor_rep}）`),
    row('投資家住所', d.investor_address),
    '',
    '*3. 振込先・日程・担保*',
    row(
      '振込先',
      `${d.bank_name} ${d.branch_name} ${d.account_type} ${d.account_number}`
    ),
    row('払込期日', d.payment_date),
    row('発行日', d.issue_date),
    row('契約日', d.contract_date),
    row('担保条件', (d.collateral || '').trim() || '無担保'),
  ].join('\n');

  return {
    type: 'modal',
    callback_id: 'shibosei_confirm',
    private_metadata: JSON.stringify(d),
    title: plainText('内容確認 4/4'),
    submit: plainText('契約書を生成'),
    close: plainText('キャンセル'),
    blocks: [
      sectionHeader('以下の内容で契約書を生成します。内容をご確認ください。\n修正したい場合はキャンセルして `/shibosei` からやり直してください。'),
      { type: 'divider' },
      { type: 'section', text: { type: 'mrkdwn', text: summary } },
    ],
  };
}

function extractValues(stateValues) {
  const out = {};
  for (const blockId of Object.keys(stateValues)) {
    for (const actionId of Object.keys(stateValues[blockId])) {
      const v = stateValues[blockId][actionId];
      if (v.type === 'plain_text_input') out[actionId] = v.value || '';
      else if (v.type === 'static_select') out[actionId] = v.selected_option?.value || '';
      else if (v.type === 'datepicker') out[actionId] = v.selected_date || '';
      else if (v.type === 'number_input') out[actionId] = v.value || '';
    }
  }
  return out;
}

module.exports = {
  buildStep1Modal,
  buildStep2Modal,
  buildStep3Modal,
  buildConfirmModal,
  extractValues,
};
