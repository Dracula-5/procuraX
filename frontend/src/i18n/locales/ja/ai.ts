import type { ai as en } from "../en/ai";

export const ai: typeof en = {
  flags: {
    title: "参考フラグ",
    note: "申請の提出時にモデルが付けた、確認者向けの参考情報です。承認ルートやポリシーの判定は変わりません。",
    none: "フラグはありません。",
    source: "{{model}} {{version}}・{{when}}",
    codes: { amount_outlier: "このカテゴリーとしては異例の金額", possible_duplicate: "重複申請の可能性" },
  },
  summary: {
    badge: "AI要約",
    note: "{{model}} が下記の出典のみから作成し、各文に出典番号を付けています。利用前に出典を確認してください。",
  },
  sql: {
    title: "SQLで質問（モデル作成）",
    hint: "言語モデルが SELECT 文を1つ作成し、サーバーが許可リストで検査したうえで、自組織のデータだけを読み取り専用で実行します。",
    placeholder: "今年度のカテゴリー別承認済み支出",
    run: "実行",
    noRows: "該当する行はありません。",
    drafted: "{{model}} が作成し、SQLガードで検査済み",
    status: { answered: "回答しました", refused: "SQLガードが拒否しました", unanswerable: "許可されたテーブルでは回答できません", no_model_answer: "モデルの回答がありません" },
    reasons: {
      llm_replay_miss: "この質問の記録済み回答がありません（リプレイモード）。新しいSQLを作成するには言語モデルの設定が必要です。",
      sql_function: "許可されていない関数が使われています。",
      sql_table: "許可されていないテーブルが使われています。",
      sql_column: "許可されていない列が使われています。",
      sql_not_select: "実行できるのは SELECT 文だけです。",
      sql_multiple_statements: "実行できる文は1つだけです。",
      sql_database_refused: "データベースが実行を拒否しました（読み取り専用・時間制限あり）。",
    },
  },
};
