'use strict';

const fs = require('fs');
const path = require('path');

function historyFilePath() {
  const dir = process.env.OUTPUT_DIR || './output';
  fs.mkdirSync(dir, { recursive: true });
  return path.join(dir, 'history.jsonl');
}

function append(entry) {
  const line =
    JSON.stringify({
      timestamp: new Date().toISOString(),
      ...entry,
    }) + '\n';
  fs.appendFileSync(historyFilePath(), line);
}

module.exports = { append };
