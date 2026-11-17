export const ai = {
  flags: {
    title: "Advisory flags",
    note: "Raised by models when the request was submitted, for the reviewer. They do not change the approval route or the policy result.",
    none: "No flags were raised.",
    source: "{{model}} {{version}} · {{when}}",
    codes: { amount_outlier: "Unusual amount for this category", possible_duplicate: "Possible duplicate request" },
  },
  summary: {
    badge: "AI summary",
    note: "Written by {{model}} from the sources below only; every sentence cites one. Check them before relying on it.",
  },
  sql: {
    title: "Ask with SQL (model-drafted)",
    hint: "A language model drafts one SELECT; the server checks it against an allow-list and runs it read-only on your organisation's data only.",
    placeholder: "Approved spend by category this fiscal year",
    run: "Run",
    noRows: "The query returned no rows.",
    drafted: "SQL drafted by {{model}} and checked by the SQL guard",
    status: { answered: "Answered", refused: "Refused by the SQL guard", unanswerable: "Not answerable from the allowed tables", no_model_answer: "No model answer available" },
    reasons: {
      llm_replay_miss: "No recorded answer for this question (replay mode). A language model must be configured to draft new SQL.",
      sql_function: "The query used a function that is not allowed.",
      sql_table: "The query used a table that is not allowed.",
      sql_column: "The query used a column that is not allowed.",
      sql_not_select: "Only SELECT queries may run.",
      sql_multiple_statements: "Only one statement may run.",
      sql_database_refused: "The database refused the query (read-only, time-limited).",
    },
  },
};
