'use strict';

require('dotenv').config();

const path = require('path');
const { App } = require('@slack/bolt');

const {
  buildStep1Modal,
  buildStep2Modal,
  buildStep3Modal,
  extractValues,
} = require('./slack-modal');
const { generateContract } = require('./generate-contract');

const requiredEnv = ['SLACK_BOT_TOKEN', 'SLACK_SIGNING_SECRET', 'SLACK_APP_TOKEN'];
for (const name of requiredEnv) {
  if (!process.env[name]) {
    console.error(`環境変数 ${name} が設定されていません。.env を確認してください。`);
    process.exit(1);
  }
}

const app = new App({
  token: process.env.SLACK_BOT_TOKEN,
  signingSecret: process.env.SLACK_SIGNING_SECRET,
  appToken: process.env.SLACK_APP_TOKEN,
  socketMode: true,
});

app.command('/shibosei', async ({ ack, body, client, logger }) => {
  await ack();
  try {
    await client.views.open({
      trigger_id: body.trigger_id,
      view: buildStep1Modal(),
    });
  } catch (e) {
    logger.error(e);
  }
});

function readMetadata(view) {
  try {
    return JSON.parse(view.private_metadata || '{}');
  } catch {
    return {};
  }
}

app.view('shibosei_step1', async ({ ack, view }) => {
  const values = extractValues(view.state.values);
  const accumulated = { ...readMetadata(view), ...values };
  await ack({
    response_action: 'update',
    view: buildStep2Modal(accumulated),
  });
});

app.view('shibosei_step2', async ({ ack, view }) => {
  const values = extractValues(view.state.values);
  const accumulated = { ...readMetadata(view), ...values };
  await ack({
    response_action: 'update',
    view: buildStep3Modal(accumulated),
  });
});

app.view('shibosei_step3', async ({ ack, body, view, client, logger }) => {
  const values = extractValues(view.state.values);
  const data = { ...readMetadata(view), ...values };
  await ack();

  const userId = body.user.id;
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
      initial_comment: `私募債契約書を生成しました（第${data.kaigo || ''}回 / ${data.issuer_name || ''}）。`,
      file_uploads: [
        { file: docxPath, filename: path.basename(docxPath), title: 'Word版契約書' },
        { file: pdfPath, filename: path.basename(pdfPath), title: 'PDF版契約書' },
      ],
    });
  } catch (e) {
    logger.error('契約書生成に失敗', e);
    await client.chat.postMessage({
      channel: dmChannelId,
      text: `契約書生成中にエラーが発生しました: \`${e.message}\``,
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
