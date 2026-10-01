import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Button, Card, ErrorNotice, Field, Input, PageHeader, Select, Spinner } from "../../components/ui";
import { api } from "../../lib/api";
import type { CostCenter, Department, User } from "../../lib/types";

export function Structure() {
  const qc = useQueryClient();
  const departments = useQuery({ queryKey: ["departments"], queryFn: () => api.get<Department[]>("/departments") });
  const costCenters = useQuery({ queryKey: ["cost-centers"], queryFn: () => api.get<CostCenter[]>("/cost-centers") });
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
  const [dept, setDept] = useState({ name: "", code: "" });
  const [cc, setCc] = useState({ department_id: "", code: "", name: "" });

  const createDept = useMutation({
    mutationFn: () => api.post<Department>("/departments", dept),
    onSuccess: () => {
      setDept({ name: "", code: "" });
      qc.invalidateQueries({ queryKey: ["departments"] });
    },
  });
  const setHead = useMutation({
    mutationFn: ({ id, head }: { id: string; head: string }) => api.patch<Department>(`/departments/${id}`, { head_user_id: head || null }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["departments"] }),
  });
  const createCc = useMutation({
    mutationFn: () => api.post<CostCenter>("/cost-centers", cc),
    onSuccess: () => {
      setCc((c) => ({ ...c, code: "", name: "" }));
      qc.invalidateQueries({ queryKey: ["cost-centers"] });
    },
  });

  if (departments.isLoading) return <Spinner />;
  const submitDept = (e: FormEvent) => {
    e.preventDefault();
    createDept.mutate();
  };
  const submitCc = (e: FormEvent) => {
    e.preventDefault();
    createCc.mutate();
  };

  return (
    <>
      <PageHeader
        title="Organisation structure"
        subtitle="Departments own cost centres and budgets. The department head approves high-value and emergency requests charged to the department."
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Departments">
          <ErrorNotice error={setHead.error} />
          <ul className="divide-y divide-border">
            {departments.data?.map((d) => (
              <li key={d.id} className="py-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-medium">
                    <span className="font-mono text-xs text-muted">{d.code}</span> {d.name}
                  </p>
                  <Select className="h-8 max-w-52" value={d.head_user_id ?? ""} onChange={(e) => setHead.mutate({ id: d.id, head: e.target.value })} aria-label={`Head of ${d.name}`}>
                    <option value="">No head assigned</option>
                    {users.data?.filter((u) => u.is_active).map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.full_name}
                      </option>
                    ))}
                  </Select>
                </div>
                <p className="mt-1 text-xs text-muted">
                  Cost centres:{" "}
                  {costCenters.data
                    ?.filter((c) => c.department_id === d.id)
                    .map((c) => c.code)
                    .join(", ") || "none"}
                </p>
              </li>
            ))}
          </ul>
          <form onSubmit={submitDept} className="mt-4 grid gap-3 border-t border-border pt-4 sm:grid-cols-[1fr_120px_auto] sm:items-end">
            <Field label="New department">
              <Input required value={dept.name} onChange={(e) => setDept((d) => ({ ...d, name: e.target.value }))} />
            </Field>
            <Field label="Code">
              <Input required pattern="[A-Za-z0-9_-]+" value={dept.code} onChange={(e) => setDept((d) => ({ ...d, code: e.target.value.toUpperCase() }))} />
            </Field>
            <Button type="submit" loading={createDept.isPending}>
              Add
            </Button>
          </form>
          <div className="mt-2">
            <ErrorNotice error={createDept.error} />
          </div>
        </Card>

        <Card title="Add a cost centre">
          <form onSubmit={submitCc} className="space-y-3">
            <Field label="Department">
              <Select required value={cc.department_id} onChange={(e) => setCc((c) => ({ ...c, department_id: e.target.value }))}>
                <option value="">— Select —</option>
                {departments.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.code} · {d.name}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Code">
                <Input required pattern="[A-Za-z0-9_-]+" value={cc.code} onChange={(e) => setCc((c) => ({ ...c, code: e.target.value.toUpperCase() }))} />
              </Field>
              <Field label="Name">
                <Input required value={cc.name} onChange={(e) => setCc((c) => ({ ...c, name: e.target.value }))} />
              </Field>
            </div>
            <ErrorNotice error={createCc.error} />
            <Button type="submit" loading={createCc.isPending}>
              Add cost centre
            </Button>
          </form>
        </Card>
      </div>
    </>
  );
}

