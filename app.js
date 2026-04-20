'use strict';

require('dotenv').config();

const path = require('path');
const { App } = require('@slack/bolt');

const {
  buildStep1Modal,
  buildStep2Modal,
  buildStep3Modal,
  buildConfirmModal,
  extractValues,
} = require('./slack-modal');
const { generateContract } = require('./generate-contract');
const {
  validateStep1,
  validateStep2,
  validateStep3,
} = require('./utils/validation');
const { isAuthorized } = require('./utils/access');
const history = require('./utils/history');

const requiredEnv = ['SLACK_BOT_TOKEN', 'SLACK_SIGNING_SECRET', 'SLACK_APP_TOKEN'];
for (const name of requiredEnv) {
  if (!process.env[name]) {
    console.error(`環境変数 ${name} が設定されていません。.env を確認してください。`);
    process.exit(1);
  }
}

const ADMIN_CHANNEL = process.env.ADMIN_CHANNEL || '';

const app = new App({
  token: process.env.SLACK_BOT_TOKEN,
  signingSecret: process.env.SLACK_SIGNING_SECRET,
  appToken: process.env.SLACK_APP_TOKEN,
  socketMode: true,
});

function readMetadata(view) {
  try {
    return JSON.parse(view.private_metadata || '{}');
  } catch {
    return {};
  }
}

function hasErrors(errors) {
  return Object.keys(errors).length > 0;
}

app.command('/shibosei', async ({ ack, body, client, logger, respond }) => {
  await ack();

  const auth = isAuthorized(body.user_id, body.channel_id);
  if (!auth.ok) {
    await respond({ response_type: 'ephemeral', text: `:warning: ${auth.reason}` });
    return;
  }

  try {
    await client.views.open({
      trigger_id: body.trigger_id,
      view: buildStep1Modal(),
    });
  } catch (e) {
    logger.error(e);
    await respond({
      response_type: 'ephemeral',
      text: 'モーダルを開けませんでした。しばらく待ってから再度お試しください。',
    });
  }
});

app.view('shibosei_step1', async ({ ack, view }) => {
  const values = extractValues(view.state.values);
  const errors = validateStep1(values);
  if (hasErrors(errors)) {
    await ack({ response_action: 'errors', errors });
    return;
  }
  const accumulated = { ...readMetadata(view), ...values };
  await ack({ response_action: 'update', view: buildStep2Modal(accumulated) });
});

app.view('shibosei_step2', async ({ ack, view }) => {
  const values = extractValues(view.state.values);
  const errors = validateStep2(values);
  if (hasErrors(errors)) {
    await ack({ response_action: 'errors', errors });
    return;
  }
  const accumulated = { ...readMetadata(view), ...values };
  await ack({ response_action: 'update', view: buildStep3Modal(accumulated) });
});

app.view('shibosei_step3', async ({ ack, view }) => {
  const values = extractValues(view.state.values);
  const errors = validateStep3(values);
  if (hasErrors(errors)) {
    await ack({ response_action: 'errors', errors });
    return;
  }
  const accumulated = { ...readMetadata(view), ...values };
  await ack({ response_action: 'update', view: buildConfirmModal(accumulated) });
});

app.view('shibosei_confirm', async ({ ack, body, view, client, logger }) => {
  const data = readMetadata(view);
  await ack();

  const userId = body.user.id;
  const userName = body.user.name || body.user.username || userId;

  let dmChannelId;
  try {
    const dm = await client.conversations.open({ users: userId });
    dmChannelId = dm.channel.id;
    await client.chat.postMessage({
      channel: dmChannelId,
      text: '契約書を生成しています。少々お待ちください...',
    });
  } catch (e) {
    logger.error('DMチャンネルを開けませんでした', e);
    return;
  }

  try {
    const { docxPath, pdfPath } = await generateContract(data);

    await client.files.uploadV2({
      channel_id: dmChannelId,
      initial_comment: `:page_facing_up: 私募債契約書を生成しました（第${data.kaigo || ''}回 / ${data.issuer_name || ''}）。`,
      file_uploads: [
        { file: docxPath, filename: path.basename(docxPath), title: 'Word版契約書' },
        { file: pdfPath, filename: path.basename(pdfPath), title: 'PDF版契約書' },
      ],
    });

    history.append({
      event: 'contract_generated',
      user_id: userId,
      user_name: userName,
      kaigo: data.kaigo,
      issuer_name: data.issuer_name,
      investor_name: data.investor_name,
      total_amount: data.total_amount,
      issue_date: data.issue_date,
      docx_path: docxPath,
      pdf_path: pdfPath,
    });

    if (ADMIN_CHANNEL) {
      try {
        await client.chat.postMessage({
          channel: ADMIN_CHANNEL,
          text: `:bookmark_tabs: 私募債契約書が生成されました\n- 作成者: <@${userId}>\n- 回号: 第${data.kaigo}回\n- 発行者: ${data.issuer_name}\n- 投資家: ${data.investor_name}\n- 発行総額: ${data.total_amount}円\n- 発行日: ${data.issue_date}`,
        });
      } catch (e) {
        logger.error('管理チャンネルへの通知に失敗', e);
      }
    }
  } catch (e) {
    logger.error('契約書生成に失敗', e);
    history.append({
      event: 'contract_failed',
      user_id: userId,
      user_name: userName,
      error: e.message,
      data,
    });
    await client.chat.postMessage({
      channel: dmChannelId,
      text: `:x: 契約書生成中にエラーが発生しました:\n\`\`\`${e.message}\`\`\``,
    });
  }
});

(async () => {
  await app.start();
  console.log('私募債契約書ボット 起動完了');
})().catch((e) => {
  console.error('ボット起動に失敗しました', e);
  process.exit(1);
});
