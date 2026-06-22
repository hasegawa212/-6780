// Gmail → Slack 投稿ウォッチャー (Google Apps Script)
//
// 使い方:
// 1. https://script.google.com で新規プロジェクト作成
// 2. このファイルの中身を Code.gs にまるごと貼り付け
// 3. 左の歯車 (Project Settings) → Script Properties に以下 2 つを追加:
//      SLACK_WEBHOOK = https://hooks.slack.com/services/...
//      ACCOUNT_NAME  = hikaruhasegawa0708@gmail.com   (任意。表示用)
// 4. 関数選択 → init を 1 回実行 (既存メールにラベル付与のみ、投稿しない)
//    → 初回認可ダイアログが出るので許可
// 5. 関数選択 → installTrigger を 1 回実行 (5 分おき監視を仕掛ける)
// 6. テスト: 自分宛にメール送信 → 5 分以内に Slack #30 に流れる
//
// 同じコードを 2 アカウント (hikaruhasegawa0708@ と 08.martialarts.20@) に
// それぞれデプロイ。Script Properties の ACCOUNT_NAME だけ変える。

const POSTED_LABEL = 'OpenClaw/Posted';
const MAX_BODY_CHARS = 600;
const SEARCH_QUERY = '-label:OpenClaw/Posted in:inbox newer_than:1d';

function watchInbox() {
  const props = PropertiesService.getScriptProperties();
  const webhook = props.getProperty('SLACK_WEBHOOK');
  const accountName = props.getProperty('ACCOUNT_NAME') || Session.getActiveUser().getEmail();
  if (!webhook) {
    throw new Error('Script property SLACK_WEBHOOK not set.');
  }

  const label = getOrCreateLabel_(POSTED_LABEL);
  const threads = GmailApp.search(SEARCH_QUERY, 0, 20);

  for (const thread of threads) {
    try {
      postThread_(thread, webhook, accountName);
      thread.addLabel(label);
    } catch (e) {
      console.error('Failed to post thread ' + thread.getId() + ': ' + e.message);
    }
  }
}

// 既存メールを「投稿済み」として無視するためにラベルだけ貼る (初回用)
function init() {
  const label = getOrCreateLabel_(POSTED_LABEL);
  const threads = GmailApp.search('in:inbox newer_than:30d -label:OpenClaw/Posted', 0, 500);
  threads.forEach(t => t.addLabel(label));
  console.log('Tagged ' + threads.length + ' existing threads as Posted.');
}

function installTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'watchInbox')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('watchInbox').timeBased().everyMinutes(5).create();
  console.log('Trigger installed: watchInbox every 5 min');
}

function uninstallTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'watchInbox')
    .forEach(t => ScriptApp.deleteTrigger(t));
  console.log('Triggers removed.');
}

function postThread_(thread, webhook, accountName) {
  const messages = thread.getMessages();
  const msg = messages[messages.length - 1];
  const from = msg.getFrom();
  const subject = thread.getFirstMessageSubject() || '(no subject)';
  const dateStr = Utilities.formatDate(msg.getDate(), 'Asia/Tokyo', 'yyyy-MM-dd HH:mm');
  const body = (msg.getPlainBody() || '').replace(/\s+/g, ' ').trim().slice(0, MAX_BODY_CHARS);
  const threadUrl = 'https://mail.google.com/mail/u/0/#inbox/' + thread.getId();

  const payload = {
    text: '[新着メール] ' + accountName + ' / ' + subject,
    blocks: [
      {
        type: 'section',
        text: { type: 'mrkdwn', text: ':email: *' + escape_(accountName) + '* に新着メール' }
      },
      {
        type: 'section',
        fields: [
          { type: 'mrkdwn', text: '*From:*\n' + escape_(from) },
          { type: 'mrkdwn', text: '*Time:*\n' + dateStr }
        ]
      },
      {
        type: 'section',
        text: { type: 'mrkdwn', text: '*件名:* ' + escape_(subject) + '\n```\n' + body + '\n```' }
      },
      {
        type: 'actions',
        elements: [
          { type: 'button', text: { type: 'plain_text', text: 'Gmail で開く' }, url: threadUrl }
        ]
      }
    ]
  };

  const res = UrlFetchApp.fetch(webhook, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  if (res.getResponseCode() !== 200) {
    throw new Error('Slack ' + res.getResponseCode() + ': ' + res.getContentText());
  }
}

function getOrCreateLabel_(name) {
  return GmailApp.getUserLabelByName(name) || GmailApp.createLabel(name);
}

function escape_(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
