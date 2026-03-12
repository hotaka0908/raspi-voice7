const functions = require("firebase-functions");
const admin = require("firebase-admin");
const OpenAI = require("openai");

admin.initializeApp();

// OpenAI クライアント（環境変数から取得）
const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

/**
 * 詳細情報についての追加質問に回答する
 */
exports.askAboutDetail = functions
  .region("asia-northeast1")
  .https.onCall(async (data, context) => {
    const { question, imageUrl, briefAnalysis, detailAnalysis } = data;

    if (!question || !question.trim()) {
      throw new functions.https.HttpsError(
        "invalid-argument",
        "質問を入力してください"
      );
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

      // 画像がある場合はVisionモデルを使用
      if (imageUrl) {
        messages[1] = {
          role: "user",
          content: [
            { type: "text", text: question },
            { type: "image_url", image_url: { url: imageUrl } },
          ],
        };
      }

      const response = await openai.chat.completions.create({
        model: imageUrl ? "gpt-4o" : "gpt-4o-mini",
        messages: messages,
        max_tokens: 500,
      });

      const answer = response.choices[0].message.content;

      return { answer };
    } catch (error) {
      console.error("OpenAI API error:", error);
      throw new functions.https.HttpsError(
        "internal",
        "回答を取得できませんでした"
      );
    }
  });
