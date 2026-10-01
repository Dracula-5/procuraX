// Mirrors the API schemas (backend/app/modules/*/schemas.py). Decimals arrive as strings.

export type Money = string;

export interface Organization {
  id: string;
  name: string;
  slug: string;
  base_currency: string;
  country: string;
  fiscal_year_start_month: number;
  timezone: string;
  is_demo: boolean;
  created_at: string;
}

export interface Me {
  id: string;
  email: string;
  full_name: string;
  job_title: string | null;
  department_id: string | null;
  manager_id: string | null;
  roles: string[];
  permissions: string[];
  organization: Organization;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: Me;
}

export type PRStatus = "draft" | "pending_approval" | "policy_blocked" | "approved" | "rejected" | "cancelled";

export interface PRSummary {
  id: string;
  number: string;
  title: string;
  status: PRStatus;
  category: string;
  currency: string;
  estimated_total: Money;
  requester_id: string;
  requester_name: string | null;
  department_id: string | null;
  department_name: string | null;
  cost_center_id: string | null;
  cost_center_code: string | null;
  preferred_vendor_id: string | null;
  vendor_name: string | null;
  is_emergency: boolean;
  required_by: string | null;
  submitted_at: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PRItem {
  id: string;
  line_no: number;
  description: string;
  quantity: string;
  unit_price: Money;
  uom: string;
  line_total: Money;
}

export interface ApprovalStep {
  id: string;
  round: number;
  sequence: number;
  approver_role: string;
  assigned_user_id: string | null;
  assigned_user_name: string | null;
  rule_ids: string[];
  reasons: string[];
  status: "waiting" | "pending" | "approved" | "rejected" | "skipped" | "cancelled";
  decided_by_id: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  comment: string | null;
  activated_at: string | null;
  due_at: string | null;
  is_overdue: boolean;
}

export interface RuleHit {
  rule_id: string;
  title: string;
  severity: "block" | "route" | "flag" | "info";
  message: string;
  evidence: Record<string, unknown>;
}

export interface PolicyEvaluation {
  engine_version: string;
  policy_version: number;
  outcome: "auto_approved" | "requires_approval" | "blocked";
  required_approvals: { role: string; sequence: number; rule_ids: string[]; reasons: string[] }[];
  hits: RuleHit[];
  budget: {
    status: "within_budget" | "near_limit" | "exceeded" | "no_budget" | "not_applicable";
    fiscal_year: number | null;
    budget_amount: Money | null;
    committed: Money | null;
    requested: Money | null;
    remaining_after: Money | null;
    utilization_after: string | null;
  };
}

export interface PRDetail extends PRSummary {
  justification: string;
  fiscal_year: number | null;
  policy_version: number | null;
  policy_evaluation: PolicyEvaluation | null;
  submission_count: number;
  cancel_reason: string | null;
  items: PRItem[];
  approvals: ApprovalStep[];
  allowed_actions: string[];
}

export interface Page<T> {
  items: T[];
  total: number;
}

export interface InboxItem {
  approval_id: string;
  purchase_request_id: string;
  number: string;
  title: string;
  requester_name: string | null;
  department_name: string | null;
  category: string;
  estimated_total: Money;
  currency: string;
  is_emergency: boolean;
  approver_role: string;
  assigned_to_me: boolean;
  rule_ids: string[];
  reasons: string[];
  activated_at: string | null;
  due_at: string | null;
  is_overdue: boolean;
}

export interface PRStats {
  by_status: Record<PRStatus, number>;
  my_open: number;
  awaiting_my_approval: number;
  overdue_approvals_for_me: number;
  approved_value_current_fy: Money;
  currency: string;
  fiscal_year: number;
}

export interface Vendor {
  id: string;
  name: string;
  legal_name: string | null;
  corporate_number: string | null;
  invoice_registration_number: string | null;
  categories: string[];
  status: "pending_review" | "approved" | "suspended" | "blocked";
  risk_level: "unknown" | "low" | "medium" | "high";
  contract_status: "none" | "active" | "expired";
  contract_expires_on: string | null;
  rating: string | null;
  contact_name: string | null;
  contact_email: string | null;
  phone: string | null;
  country: string;
  currency: string;
  payment_terms_days: number;
  website: string | null;
  notes: string | null;
  status_changed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Department {
  id: string;
  name: string;
  code: string;
  head_user_id: string | null;
  head_name: string | null;
  created_at: string;
}

export interface CostCenter {
  id: string;
  department_id: string;
  code: string;
  name: string;
  is_active: boolean;
}

export interface Budget {
  id: string;
  cost_center_id: string;
  cost_center_code: string;
  cost_center_name: string;
  department_id: string;
  fiscal_year: number;
  amount: Money;
  currency: string;
  notes: string | null;
  committed: Money;
  pending: Money;
  available: Money;
  utilization: string | null;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  job_title: string | null;
  department_id: string | null;
  manager_id: string | null;
  roles: string[];
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Invitation {
  id: string;
  email: string;
  full_name: string | null;
  roles: string[];
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
  invite_url: string | null;
}

export interface RoleDef {
  key: string;
  name: string;
  description: string;
  permissions: string[];
  assignable: boolean;
}

export interface AuditEntry {
  id: string;
  seq: number;
  actor_user_id: string | null;
  actor_email: string | null;
  actor_type: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  summary: string;
  changes: Record<string, unknown> | null;
  metadata: Record<string, unknown> | null;
  request_id: string | null;
  created_at: string;
}

export interface PolicyConfig {
  currency: string;
  auto_approval_limit: Money;
  manager_approval_limit: Money;
  procurement_review_threshold: Money;
  procurement_review_categories: string[];
  contract_required_categories: string[];
  budget_warning_utilization: string;
  split_purchase_window_days: number;
  emergency_min_justification_chars: number;
  approval_sla_hours: number;
}

export interface PolicyVersion {
  id: string;
  version: number;
  is_active: boolean;
  config: PolicyConfig;
  notes: string | null;
  created_by_id: string | null;
  created_at: string;
}

export interface Rule {
  id: string;
  title: string;
  description: string;
}

export interface DemoPersona {
  email: string;
  full_name: string;
  job_title: string | null;
  roles: string[];
}

