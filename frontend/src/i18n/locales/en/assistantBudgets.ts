export const assistant = {
  title: "Procurement assistant",
  subtitle: "Answers use fixed, tenant-scoped queries and link each result to source records. Unsupported questions return no guessed answer.",
  askTitle: "Ask about procurement data",
  question: "Question",
  placeholder: "Show software purchases above ¥1,000,000",
  try: "Try: unmatched invoices · approved vendors for software · pending payment approvals · why is PR-2026-000042 blocked?",
  query: "Query",
  supported: "Supported query types",
  noMatchTitle: "No matching records",
  noMatchBody: "No evidence matched the supported query in this tenant.",
};

export const knowledge = {
  title: "Policy and guidance search",
  hint: "Searches the approval policy rules and your organisation's documents. Returns cited excerpts, never a generated answer.",
  placeholder: "適格請求書の記載事項は？ / When is a second quote required?",
  noMatchTitle: "No cited source matched",
  noMatchBody: "Try different wording or add the relevant guideline.",
  docsTitle: "Organisation guidelines",
  docsIntro:
    "Plain-text policies or guidance (Japanese or English). Adding a document with an existing title creates a new version; earlier citations keep their version.",
  source: "Source (optional)",
  sourcePlaceholder: "出典：国税庁「…」を加工して作成",
  text: "Text",
  add: "Add document",
};

export const budgets = {
  title: "Budgets",
  subtitle: "Committed = approved requests; pending = awaiting approval. Both are computed live from requests, never stored.",
  setTitle: "Set a budget",
  amountIn: "Amount ({{currency}})",
  emptyTitle: "No budgets defined",
  emptyBody: "Requests on cost centres without a budget are routed to finance (rule BUD-001).",
  fy: "FY",
  committed: "Committed",
  pending: "Pending",
  available: "Available",
  utilisation: "Utilisation",
};
