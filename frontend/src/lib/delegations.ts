// Delegation records as the API returns them, and their display state.
export type Delegation = {
  id: string;
  approval_id: string;
  purchase_request_id: string;
  request_number: string;
  request_title: string;
  approver_role: string;
  step_status: string;
  delegator_id: string;
  delegator_name: string;
  delegate_id: string;
  delegate_name: string;
  delegated_at: string;
  revoked_at: string | null;
  can_revoke: boolean;
};

export type DelegationState = "active" | "revoked" | "completed";

export function delegationState(d: Pick<Delegation, "revoked_at" | "step_status">): DelegationState {
  if (d.revoked_at) return "revoked";
  return d.step_status === "pending" || d.step_status === "waiting" ? "active" : "completed";
}
