import type { assistant as enAssistant, budgets as enBudgets, knowledge as enKnowledge } from "../en/assistantBudgets";

export const assistant: typeof enAssistant = {
  title: "調達アシスタント",
  subtitle: "回答はテナント内に限定した定型クエリで作成し、各結果を元のレコードにリンクします。対応外の質問には推測で答えません。",
  askTitle: "調達データについて質問する",
  question: "質問",
  placeholder: "100万円を超えるソフトウェア購入を表示",
  try: "例：未照合の請求書・ソフトウェアの承認済み取引先・支払承認待ち・PR-2026-000042 がブロックされた理由",
  query: "検索",
  supported: "対応しているクエリの種類",
  noMatchTitle: "該当するレコードはありません",
  noMatchBody: "このテナントでは、対応クエリに一致する根拠が見つかりませんでした。",
};

export const knowledge: typeof enKnowledge = {
  title: "ポリシー・ガイドライン検索",
  hint: "承認ポリシーのルールと自組織の文書を検索します。生成した回答ではなく、出典付きの抜粋を返します。",
  placeholder: "適格請求書の記載事項は？ / 相見積が必要なのはいつ？",
  noMatchTitle: "該当する出典はありません",
  noMatchBody: "表現を変えるか、関連するガイドラインを追加してください。",
  docsTitle: "組織のガイドライン",
  docsIntro:
    "テキスト形式のポリシーやガイドライン（日本語・英語）。同じ件名で追加すると新しい版になり、過去の引用は元の版を参照し続けます。",
  source: "出典（任意）",
  sourcePlaceholder: "出典：国税庁「…」を加工して作成",
  text: "本文",
  add: "文書を追加",
};

export const budgets: typeof enBudgets = {
  title: "予算",
  subtitle: "確定＝承認済みの申請、未確定＝承認待ちの申請。いずれも申請からその都度算出し、保存しません。",
  setTitle: "予算を設定",
  amountIn: "金額（{{currency}}）",
  emptyTitle: "予算が設定されていません",
  emptyBody: "予算のないコストセンターへの申請は経理に回付されます（ルール BUD-001）。",
  fy: "年度",
  committed: "確定",
  pending: "未確定",
  available: "利用可能",
  utilisation: "消化率",
};
