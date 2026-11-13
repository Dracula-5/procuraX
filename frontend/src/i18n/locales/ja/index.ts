import type { Messages } from "../en";
import { assistant, budgets, knowledge } from "./assistantBudgets";
import { app, audit, auth, badges, bank, common, dashboard, delegations, errors, landing, layout, nav, notice, paymentSettings, policy, structure, users } from "./core";
import { contracts, fx, ledger } from "./finance";
import { inbox, vendors } from "./inboxVendors";
import { labels } from "./labels";
import { notifications } from "./notifications";
import { security } from "./security";
import { p2p } from "./p2p";
import { policyPanel, requestDetail } from "./requestDetail";
import { requestForm, requests } from "./requests";

/** Japanese UI strings (日本語). Typed as the English messages: a missing or extra key fails the build. */
export const ja: Messages = {
  common,
  app,
  nav,
  layout,
  notice,
  badges,
  landing,
  auth,
  dashboard,
  requests,
  requestForm,
  requestDetail,
  policyPanel,
  inbox,
  vendors,
  p2p,
  assistant,
  knowledge,
  budgets,
  policy,
  audit,
  users,
  structure,
  delegations,
  bank,
  paymentSettings,
  errors,
  labels,
  notifications,
  contracts,
  fx,
  ledger,
  security,
};


