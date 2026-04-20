'use strict';

function parseList(raw) {
  return (raw || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function isAuthorized(userId, channelId) {
  const allowedUsers = parseList(process.env.ALLOWED_USERS);
  const allowedChannels = parseList(process.env.ALLOWED_CHANNELS);

  if (allowedUsers.length > 0 && !allowedUsers.includes(userId)) {
    return { ok: false, reason: 'ユーザーがこのボットの利用を許可されていません。管理者にお問い合わせください。' };
  }
  if (allowedChannels.length > 0 && !allowedChannels.includes(channelId)) {
    return { ok: false, reason: 'このチャンネルからは /shibosei を実行できません。許可されたチャンネルで実行してください。' };
  }
  return { ok: true };
}

module.exports = { isAuthorized };
