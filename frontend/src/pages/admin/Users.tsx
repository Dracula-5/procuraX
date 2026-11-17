import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../auth/useAuth";
import { RoleBadge } from "../../components/badges";
import { Badge, Button, Card, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { dateTime, relative } from "../../lib/format";
import { useLabels } from "../../lib/labels";
import type { Department, Invitation, RoleDef, User } from "../../lib/types";

export function UsersAdmin() {
  const { t } = useTranslation();
  const labels = useLabels();
  const qc = useQueryClient();
  const { me } = useAuth();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api.get<RoleDef[]>("/roles") });
  const departments = useQuery({ queryKey: ["departments"], queryFn: () => api.get<Department[]>("/departments") });
  const invitations = useQuery({ queryKey: ["invitations"], queryFn: () => api.get<Invitation[]>("/invitations") });
  const [editing, setEditing] = useState<User | null>(null);
  const [inviting, setInviting] = useState(false);
  const [lastInvite, setLastInvite] = useState<Invitation | null>(null);

  const deptName = (id: string | null) => departments.data?.find((d) => d.id === id)?.name ?? "—";
  const userName = (id: string | null) => users.data?.find((u) => u.id === id)?.full_name ?? "—";
  const assignable = roles.data?.filter((r) => r.assignable) ?? [];
  const pendingInvites = invitations.data?.filter((i) => !i.accepted_at && !i.revoked_at) ?? [];

  const revoke = useMutation({
    mutationFn: (id: string) => api.del(`/invitations/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invitations"] }),
  });
  const resetMfa = useMutation({
    mutationFn: (id: string) => api.post(`/users/${id}/mfa/reset`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });

  if (users.isLoading || roles.isLoading) return <Spinner />;

  return (
    <>
      <PageHeader
        title={t("users.title")}
        subtitle={t("users.subtitle")}
        actions={
          <Button onClick={() => (setInviting((v) => !v), setLastInvite(null))}>
            <UserPlus className="size-4" /> {t("users.invite")}
          </Button>
        }
      />

      {lastInvite?.invite_url && (
        <div className="mb-6 rounded-md border border-ok/30 bg-ok-soft p-3 text-sm">
          <p className="font-medium text-ok">{t("users.invitationCreated", { email: lastInvite.email })}</p>
          <p className="mt-1 text-muted">{t("users.shareLink", { expires: relative(lastInvite.expires_at) })}</p>
          <div className="mt-2 flex gap-2">
            <Input readOnly value={lastInvite.invite_url} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
            <Button variant="secondary" onClick={() => navigator.clipboard?.writeText(lastInvite.invite_url ?? "")} aria-label={t("users.copyLink")}>
              <Copy className="size-4" />
            </Button>
          </div>
        </div>
      )}

      {inviting && (
        <InviteForm
          roles={assignable}
          departments={departments.data ?? []}
          users={users.data ?? []}
          onDone={(inv) => {
            setInviting(false);
            setLastInvite(inv);
            qc.invalidateQueries({ queryKey: ["invitations"] });
          }}
        />
      )}

      {editing && (
        <EditUser
          user={editing}
          roles={assignable}
          departments={departments.data ?? []}
          users={users.data ?? []}
          isSelf={editing.id === me?.id}
          onDone={() => {
            setEditing(null);
            qc.invalidateQueries({ queryKey: ["users"] });
          }}
        />
      )}

      <ErrorNotice error={resetMfa.error} />
      <Table>
        <thead>
          <tr>
            <Th>{t("users.col.user")}</Th>
            <Th>{t("users.col.roles")}</Th>
            <Th>{t("users.department")}</Th>
            <Th>{t("users.reportsTo")}</Th>
            <Th>{t("users.col.lastSignIn")}</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {users.data?.map((u) => (
            <tr key={u.id} className={u.is_active ? "" : "opacity-60"}>
              <Td>
                <p className="font-medium">
                  {u.full_name} {!u.is_active && <Badge>{t("common.inactive")}</Badge>}{" "}
                  {u.mfa_enabled && <Badge tone="ok">{t("security.admin.mfaOn")}</Badge>}
                </p>
                <p className="text-xs text-muted">{u.email}</p>
              </Td>
              <Td>
                <div className="flex flex-wrap gap-1">
                  {u.roles.map((r) => (
                    <RoleBadge key={r} role={r} />
                  ))}
                </div>
              </Td>
              <Td>{deptName(u.department_id)}</Td>
              <Td>{userName(u.manager_id)}</Td>
              <Td className="whitespace-nowrap text-muted">{u.last_login_at ? relative(u.last_login_at) : t("users.never")}</Td>
              <Td className="whitespace-nowrap">
                <Button size="sm" variant="ghost" onClick={() => setEditing(u)}>
                  {t("common.edit")}
                </Button>
                {u.mfa_enabled && u.id !== me?.id && (
                  <Button
                    size="sm"
                    variant="ghost"
                    loading={resetMfa.isPending && resetMfa.variables === u.id}
                    onClick={() => window.confirm(t("security.admin.resetConfirm", { name: u.full_name })) && resetMfa.mutate(u.id)}
                  >
                    {t("security.admin.resetMfa")}
                  </Button>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>

      {pendingInvites.length > 0 && (
        <Card title={t("users.pendingInvitations")} className="mt-6">
          <ErrorNotice error={revoke.error} />
          <ul className="divide-y divide-border text-sm">
            {pendingInvites.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  {i.email} · <span className="text-muted">{i.roles.map((r) => labels.role(r)).join(t("common.listSeparator"))}</span>
                </span>
                <span className="flex items-center gap-2 text-xs text-muted">
                  {t("users.expires", { when: dateTime(i.expires_at) })}
                  <Button size="sm" variant="ghost" onClick={() => revoke.mutate(i.id)}>
                    {t("users.revoke")}
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

function RolePicker({ roles, value, onChange }: { roles: RoleDef[]; value: string[]; onChange: (v: string[]) => void }) {
  const labels = useLabels();
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {roles.map((r) => (
        <label key={r.key} className="flex items-start gap-2 rounded-md border border-border p-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 accent-[var(--primary)]"
            checked={value.includes(r.key)}
            onChange={() => onChange(value.includes(r.key) ? value.filter((k) => k !== r.key) : [...value, r.key])}
          />
          <span>
            <span className="font-medium">{labels.role(r.key)}</span>
            <span className="block text-xs text-muted">{labels.roleDescription(r.key, r.description)}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

interface FormProps {
  roles: RoleDef[];
  departments: Department[];
  users: User[];
}

function InviteForm({ roles, departments, users, onDone }: FormProps & { onDone: (inv: Invitation) => void }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ email: "", full_name: "", roles: ["employee"], department_id: "", manager_id: "", job_title: "" });
  const invite = useMutation({
    mutationFn: () =>
      api.post<Invitation>("/invitations", {
        ...form,
        department_id: form.department_id || null,
        manager_id: form.manager_id || null,
        full_name: form.full_name || null,
        job_title: form.job_title || null,
      }),
    onSuccess: onDone,
  });
  const errors = invite.error instanceof ApiError ? invite.error.fieldErrors() : {};
  const submit = (e: FormEvent) => {
    e.preventDefault();
    invite.mutate();
  };
  return (
    <Card title={t("users.inviteTitle")} className="mb-6">
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("auth.email")} error={errors.email}>
            <Input required type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
          </Field>
          <Field label={t("users.jobTitle")}>
            <Input value={form.job_title} onChange={(e) => setForm((f) => ({ ...f, job_title: e.target.value }))} />
          </Field>
          <Field label={t("users.department")}>
            <Select value={form.department_id} onChange={(e) => setForm((f) => ({ ...f, department_id: e.target.value }))}>
              <option value="">—</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.code} · {d.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("users.reportsTo")} hint={t("users.reportsToHint")}>
            <Select value={form.manager_id} onChange={(e) => setForm((f) => ({ ...f, manager_id: e.target.value }))}>
              <option value="">—</option>
              {users.filter((u) => u.is_active).map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <RolePicker roles={roles} value={form.roles} onChange={(v) => setForm((f) => ({ ...f, roles: v }))} />
        {!Object.keys(errors).length && <ErrorNotice error={invite.error} />}
        <Button type="submit" loading={invite.isPending} disabled={!form.roles.length}>
          {t("users.createInvitation")}
        </Button>
      </form>
    </Card>
  );
}

function EditUser({ user, roles, departments, users, isSelf, onDone }: FormProps & { user: User; isSelf: boolean; onDone: () => void }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    roles: user.roles,
    department_id: user.department_id ?? "",
    manager_id: user.manager_id ?? "",
    is_active: user.is_active,
  });
  const save = useMutation({
    mutationFn: () =>
      api.patch<User>(`/users/${user.id}`, {
        roles: form.roles,
        department_id: form.department_id || null,
        manager_id: form.manager_id || null,
        is_active: form.is_active,
      }),
    onSuccess: onDone,
  });
  return (
    <Card title={t("users.editTitle", { name: user.full_name })} className="mb-6">
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("users.department")}>
            <Select value={form.department_id} onChange={(e) => setForm((f) => ({ ...f, department_id: e.target.value }))}>
              <option value="">—</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.code} · {d.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("users.reportsTo")}>
            <Select value={form.manager_id} onChange={(e) => setForm((f) => ({ ...f, manager_id: e.target.value }))}>
              <option value="">—</option>
              {users
                .filter((u) => u.id !== user.id && u.is_active)
                .map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.full_name}
                  </option>
                ))}
            </Select>
          </Field>
        </div>
        <RolePicker roles={roles} value={form.roles} onChange={(v) => setForm((f) => ({ ...f, roles: v }))} />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-[var(--primary)]" disabled={isSelf} checked={form.is_active} onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))} />
          {t("common.active")} {isSelf && <span className="text-muted">{t("users.cannotDeactivateSelf")}</span>}
        </label>
        <ErrorNotice error={save.error} />
        <div className="flex gap-2">
          <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!form.roles.length}>
            {t("common.save")}
          </Button>
          <Button variant="ghost" onClick={onDone}>
            {t("common.close")}
          </Button>
        </div>
      </div>
    </Card>
  );
}
