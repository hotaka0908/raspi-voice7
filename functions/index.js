const { onRequest } = require("firebase-functions/v2/https");
const { setGlobalOptions } = require("firebase-functions/v2");
const admin = require("firebase-admin");
const OpenAI = require("openai");

admin.initializeApp();

// グローバル設定
setGlobalOptions({ region: "us-central1" });

// OpenAI クライアントを遅延初期化
let openai = null;
function getOpenAI() {
  if (!openai) {
    openai = new OpenAI({
      apiKey: process.env.OPENAI_API_KEY,
    });
  }
  return openai;
}

/**
 * 詳細情報についての追加質問に回答する
 */
exports.askAboutDetail = onRequest({ cors: true }, async (req, res) => {
  // POSTのみ許可
  if (req.method !== "POST") {
    res.status(405).send("Method Not Allowed");
    return;
  }

  const { question, imageUrl, briefAnalysis, detailAnalysis } = req.body;

  if (!question || !question.trim()) {
    res.status(400).json({ error: "質問を入力してください" });
    return;
  }

  try {
    const messages = [
      {
        role: "system",
        content: `あなたは親切なアシスタントです。ユーザーが以前見たものについて追加の質問をしています。
以下の情報を踏まえて、質問に簡潔に回答してください。

■ 以前の回答:
${briefAnalysis || ""}

■ 詳細情報:
${detailAnalysis || ""}

回答は日本語で、2-3文程度で簡潔にお願いします。`,
      },
      {
        role: "user",
        content: question,
      },
    ];

    const response = await getOpenAI().chat.completions.create({
      model: "gpt-4o-mini",
      messages: messages,
      max_tokens: 500,
    });

    const answer = response.choices[0].message.content;

    res.json({ answer });
  } catch (error) {
    console.error("OpenAI API error:", error);
    res.status(500).json({ error: "回答を取得できませんでした" });
  }
});
