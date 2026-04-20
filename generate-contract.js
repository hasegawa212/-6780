'use strict';

const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const {
  Document,
  Packer,
  Paragraph,
  TextRun,
  AlignmentType,
} = require('docx');

function safe(v) {
  return (v === undefined || v === null) ? '' : String(v);
}

function sanitizeFilename(name) {
  return safe(name).replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40) || 'unknown';
}

const BODY_FONT = 'MS 明朝';
const HEADING_FONT = 'MS ゴシック';

function para(text, opts = {}) {
  return new Paragraph({
    alignment: opts.align || AlignmentType.LEFT,
    spacing: { after: opts.after ?? 80 },
    children: [
      new TextRun({
        text: safe(text),
        bold: !!opts.bold,
        size: opts.size,
        font: opts.bold ? HEADING_FONT : BODY_FONT,
      }),
    ],
  });
}

function buildDocument(d) {
  const children = [
    para(`第${safe(d.kaigo)}回 私募債 総額引受契約書`, {
      align: AlignmentType.CENTER,
      bold: true,
      size: 32,
      after: 400,
    }),
    para(
      `発行者 ${safe(d.issuer_name)}（以下「甲」という。）と投資家 ${safe(d.investor_name)}（以下「乙」という。）は、甲が発行する第${safe(d.kaigo)}回社債（以下「本社債」という。）の総額引受に関し、以下のとおり契約（以下「本契約」という。）を締結する。`,
      { after: 300 }
    ),

    para('第1条（社債の発行）', { bold: true }),
    para('甲は、下記要領に従い本社債を発行し、乙はこれを引き受ける。'),
    para(`　1. 発行総額: 金${safe(d.total_amount)}円`),
    para(`　2. 各社債の金額: 金${safe(d.unit_amount)}円`),
    para(`　3. 発行日: ${safe(d.issue_date)}`),
    para(`　4. 償還期限: ${safe(d.maturity_date)}`),
    para(`　5. 利率: 年${safe(d.interest_rate)}%`),
    para(`　6. 利払日: ${safe(d.interest_pay_date)}`),
    para(`　7. 償還方法: ${safe(d.redemption_method)}`, { after: 200 }),

    para('第2条（払込）', { bold: true }),
    para(
      `乙は、${safe(d.payment_date)}までに本社債の発行総額金${safe(d.total_amount)}円を、甲が指定する下記口座に振り込む方法により払い込むものとする。なお、振込手数料は乙の負担とする。`
    ),
    para(`　銀行名: ${safe(d.bank_name)} ${safe(d.branch_name)}`),
    para(`　口座種類: ${safe(d.account_type)}`),
    para(`　口座番号: ${safe(d.account_number)}`),
    para(`　口座名義: ${safe(d.issuer_name)}`, { after: 200 }),

    para('第3条（利息）', { bold: true }),
    para(
      `本社債の利息は、発行日の翌日から償還期限までの期間について、年${safe(d.interest_rate)}%の割合をもって計算し、${safe(d.interest_pay_date)}に支払うものとする。利息の計算は、1年を365日とする日割計算とする。`,
      { after: 200 }
    ),

    para('第4条（償還）', { bold: true }),
    para(
      `本社債は、${safe(d.maturity_date)}に${safe(d.redemption_method)}の方法により、額面金額をもって償還する。`,
      { after: 200 }
    ),

    para('第5条（担保）', { bold: true }),
    para(
      d.collateral && d.collateral.trim()
        ? safe(d.collateral)
        : '本社債は無担保とする。',
      { after: 200 }
    ),

    para('第6条（期限の利益喪失）', { bold: true }),
    para('甲に次の各号に該当する事由が生じた場合、甲は当然に本社債の期限の利益を失い、直ちに未償還元本及び経過利息を乙に支払うものとする。'),
    para('　(1) 甲が本契約上の義務を履行しないとき'),
    para('　(2) 甲が破産、民事再生、会社更生その他の倒産手続の申立てを行い、又は申立てがなされたとき'),
    para('　(3) 甲の財産について差押え、仮差押え、仮処分又は強制執行がなされたとき'),
    para('　(4) 甲が手形若しくは小切手の不渡りを出したとき、又は支払停止の状態となったとき', {
      after: 200,
    }),

    para('第7条（通知）', { bold: true }),
    para(
      '本契約に基づく通知は、書面又は電磁的方法により相手方に対して行うものとし、各当事者は通知先に変更があった場合には速やかに相手方に通知するものとする。',
      { after: 200 }
    ),

    para('第8条（合意管轄）', { bold: true }),
    para(
      '本契約に関する一切の紛争については、東京地方裁判所を第一審の専属的合意管轄裁判所とする。',
      { after: 200 }
    ),

    para('第9条（協議事項）', { bold: true }),
    para(
      '本契約に定めのない事項又は本契約の解釈について疑義が生じた場合、甲乙誠実に協議の上これを解決するものとする。',
      { after: 400 }
    ),

    para('本契約締結の証として、本書2通を作成し、甲乙記名押印の上、各自1通を保有する。', {
      after: 300,
    }),

    para(safe(d.contract_date), { align: AlignmentType.CENTER, after: 400 }),

    para('甲（発行者）', { bold: true }),
    para(`　住所: ${safe(d.issuer_address)}`),
    para(`　名称: ${safe(d.issuer_name)}`),
    para(`　　　　${safe(d.issuer_rep)}　　印`, { after: 300 }),

    para('乙（投資家）', { bold: true }),
    para(`　住所: ${safe(d.investor_address)}`),
    para(`　名称: ${safe(d.investor_name)}`),
    para(`　　　　${safe(d.investor_rep)}　　印`),
  ];

  return new Document({
    creator: '私募債契約書ボット',
    title: `第${safe(d.kaigo)}回 私募債 総額引受契約書`,
    styles: {
      default: {
        document: {
          run: { font: BODY_FONT, size: 22 },
        },
      },
    },
    sections: [
      {
        properties: {
          page: {
            margin: { top: 1200, right: 1200, bottom: 1200, left: 1200 },
          },
        },
        children,
      },
    ],
  });
}

function convertToPdf(docxPath, outputDir) {
  return new Promise((resolve, reject) => {
    execFile(
      'libreoffice',
      ['--headless', '--convert-to', 'pdf', '--outdir', outputDir, docxPath],
      { timeout: 60000 },
      (err, stdout, stderr) => {
        if (err) {
          return reject(
            new Error(
              `LibreOfficeでのPDF変換に失敗しました。libreoffice-writerがインストールされているか確認してください。詳細: ${stderr || err.message}`
            )
          );
        }
        resolve();
      }
    );
  });
}

async function generateContract(data) {
  const outputDir = process.env.OUTPUT_DIR || './output';
  fs.mkdirSync(outputDir, { recursive: true });

  const baseName = `私募債契約書_${sanitizeFilename(data.issuer_name)}_第${sanitizeFilename(data.kaigo)}回_${Date.now()}`;
  const docxPath = path.join(outputDir, `${baseName}.docx`);
  const pdfPath = path.join(outputDir, `${baseName}.pdf`);

  const doc = buildDocument(data);
  const buffer = await Packer.toBuffer(doc);
  fs.writeFileSync(docxPath, buffer);

  await convertToPdf(docxPath, outputDir);

  if (!fs.existsSync(pdfPath)) {
    throw new Error(`PDFが生成されませんでした: ${pdfPath}`);
  }

  return { docxPath, pdfPath };
}

module.exports = { generateContract };
