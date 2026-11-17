import type { inbox as enInbox, vendors as enVendors } from "../en/inboxVendors";

export const inbox: typeof enInbox = {
  title: "承認受信箱",
  subtitle: "あなたの対応を待っているステップです。組織構造上あなたに割り当てられたもの、または所属チームのキューのもので、期限の近い順に表示します。",
  delegations: "代理承認の委任",
  emptyTitle: "承認待ちはありません",
  emptyBody: "ポリシーにより申請があなたに回付されると、ここに表示されます。",
  whyYou: "回付理由",
  assignedToYou: "あなたに割当",
  teamQueue: "チームのキュー",
};

export const vendors: typeof enVendors = {
  title: "取引先",
  subtitle: "取引先マスタ。新規取引先はオンボーディング審査から始まり、承認は権限を持つ担当者が別途行います。",
  onboard: "取引先を登録",
  searchPlaceholder: "名称で検索",
  empty: "取引先が見つかりません",
  col: { vendor: "取引先", categories: "カテゴリ", onboarded: "登録日", changeStatus: "ステータス変更" },
  noRegistration: "インボイス登録番号なし",
  terms: "支払サイト {{days}} 日",
  bankAccount: "振込口座",
  changeStatusOf: "{{name}} のステータスを変更",
  set: "変更…",
  reasonPrompt: "{{name}} を「{{status}}」にする理由を入力してください",
  form: {
    title: "取引先を登録",
    name: "名称",
    registration: "インボイス登録番号（適格請求書発行事業者登録番号）",
    registrationHint: "適格請求書等保存方式：T＋13桁",
    risk: "リスク評価",
    contract: "契約",
    email: "連絡先メールアドレス",
    terms: "支払サイト（日）",
    categories: "取扱カテゴリ",
    create: "登録する（審査待ち）",
  },
};
