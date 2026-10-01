import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useAuth } from "../../auth/useAuth";
import { RoleBadge } from "../../components/badges";
import { Badge, Button, Card, ErrorNotice, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { dateTime, relative } from "../../lib/format";
import type { Department, Invitation, RoleDef, User } from "../../lib/types";

export function UsersAdmin() {
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

  if (users.isLoading || roles.isLoading) return <Spinner />;

  return (
    <>
      <PageHeader
        title="Users & roles"
        subtitle="Roles grant permissions; the reporting line (manager) and department heads decide who approves what."
        actions={
          <Button onClick={() => (setInviting((v) => !v), setLastInvite(null))}>
            <UserPlus className="size-4" /> Invite user
          </Button>
        }
      />

      {lastInvite?.invite_url && (
        <div className="mb-6 rounded-md border border-ok/30 bg-ok-soft p-3 text-sm">
          <p className="font-medium text-ok">Invitation created for {lastInvite.email}</p>
          <p className="mt-1 text-muted">
            E-mail delivery is not connected yet (integration roadmap). Share this single-use link; it expires {relative(lastInvite.expires_at)}.
          </p>
          <div className="mt-2 flex gap-2">
            <Input readOnly value={lastInvite.invite_url} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
            <Button variant="secondary" onClick={() => navigator.clipboard?.writeText(lastInvite.invite_url ?? "")} aria-label="Copy link">
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

      <Table>
        <thead>
          <tr>
            <Th>User</Th>
            <Th>Roles</Th>
            <Th>Department</Th>
            <Th>Reports to</Th>
            <Th>Last sign-in</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {users.data?.map((u) => (
            <tr key={u.id} className={u.is_active ? "" : "opacity-60"}>
              <Td>
                <p className="font-medium">
                  {u.full_name} {!u.is_active && <Badge>inactive</Badge>}
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
              <Td className="whitespace-nowrap text-muted">{u.last_login_at ? relative(u.last_login_at) : "never"}</Td>
              <Td>
                <Button size="sm" variant="ghost" onClick={() => setEditing(u)}>
                  Edit
                </Button>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>

      {pendingInvites.length > 0 && (
        <Card title="Pending invitations" className="mt-6">
          <ErrorNotice error={revoke.error} />
          <ul className="divide-y divide-border text-sm">
            {pendingInvites.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  {i.email} · <span className="text-muted">{i.roles.join(", ")}</span>
                </span>
                <span className="flex items-center gap-2 text-xs text-muted">
                  expires {dateTime(i.expires_at)}
                  <Button size="sm" variant="ghost" onClick={() => revoke.mutate(i.id)}>
                    Revoke
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
            <span className="font-medium">{r.name}</span>
            <span className="block text-xs text-muted">{r.description}</span>
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
    <Card title="Invite a user" className="mb-6">
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="E-mail" error={errors.email}>
            <Input required type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
          </Field>
          <Field label="Job title">
            <Input value={form.job_title} onChange={(e) => setForm((f) => ({ ...f, job_title: e.target.value }))} />
          </Field>
          <Field label="Department">
            <Select value={form.department_id} onChange={(e) => setForm((f) => ({ ...f, department_id: e.target.value }))}>
              <option value="">—</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.code} · {d.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Reports to" hint="Their manager approves their requests (rule APR-001).">
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
          Create invitation
        </Button>
      </form>
    </Card>
  );
}

function EditUser({ user, roles, departments, users, isSelf, onDone }: FormProps & { user: User; isSelf: boolean; onDone: () => void }) {
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
    <Card title={`Edit ${user.full_name}`} className="mb-6">
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Department">
            <Select value={form.department_id} onChange={(e) => setForm((f) => ({ ...f, department_id: e.target.value }))}>
              <option value="">—</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.code} · {d.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Reports to">
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
          Active {isSelf && <span className="text-muted">(you cannot deactivate yourself)</span>}
        </label>
        <ErrorNotice error={save.error} />
        <div className="flex gap-2">
          <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!form.roles.length}>
            Save
          </Button>
          <Button variant="ghost" onClick={onDone}>
            Close
          </Button>
        </div>
      </div>
    </Card>
  );
}

