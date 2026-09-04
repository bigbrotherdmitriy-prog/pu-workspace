export type ContractTreeItem = {
  id: number;
  number: string;
  title: string;
  counterparty?: string;
  contract_kind?: string;
  parent_contract_id?: number;
};

export type ContractTreeRow<T> = {
  item: T;
  depth: number;
  hasChildren: boolean;
  parentMissing: boolean;
};

const KIND_ORDER: Record<string, number> = {
  prime_reference: 0,
  customer: 1,
  revenue_subcontract: 2,
  downstream_subcontract: 3,
  supply: 4,
};

export function buildContractTree<T extends ContractTreeItem>(contracts: T[], query = ""): ContractTreeRow<T>[] {
  const byId = new Map(contracts.map((item) => [item.id, item]));
  const included = new Set<number>();
  const normalizedQuery = query.trim().toLocaleLowerCase("ru-RU");
  const includeWithAncestors = (item: T) => {
    let current: T | undefined = item;
    const path = new Set<number>();
    while (current && !path.has(current.id)) {
      path.add(current.id);
      included.add(current.id);
      current = current.parent_contract_id ? byId.get(current.parent_contract_id) : undefined;
    }
  };
  contracts.forEach((item) => {
    const text = `${item.number} ${item.title} ${item.counterparty || ""}`.toLocaleLowerCase("ru-RU");
    if (!normalizedQuery || text.includes(normalizedQuery)) includeWithAncestors(item);
  });

  const compare = (left: T, right: T) =>
    (KIND_ORDER[left.contract_kind || ""] ?? 99) - (KIND_ORDER[right.contract_kind || ""] ?? 99)
    || left.number.localeCompare(right.number, "ru", { numeric: true });
  const children = new Map<number, T[]>();
  contracts.filter((item) => included.has(item.id)).forEach((item) => {
    if (!item.parent_contract_id || !included.has(item.parent_contract_id)) return;
    children.set(item.parent_contract_id, [...(children.get(item.parent_contract_id) || []), item]);
  });
  children.forEach((items) => items.sort(compare));
  const roots = contracts.filter((item) => included.has(item.id) && (!item.parent_contract_id || !included.has(item.parent_contract_id))).sort(compare);
  const rows: ContractTreeRow<T>[] = [];
  const visited = new Set<number>();
  const append = (item: T, depth: number) => {
    if (visited.has(item.id)) return;
    visited.add(item.id);
    const descendants = children.get(item.id) || [];
    rows.push({ item, depth, hasChildren: descendants.length > 0, parentMissing: Boolean(item.parent_contract_id && !byId.has(item.parent_contract_id)) });
    descendants.forEach((child) => append(child, depth + 1));
  };
  roots.forEach((root) => append(root, 0));
  contracts.filter((item) => included.has(item.id) && !visited.has(item.id)).sort(compare).forEach((item) => append(item, 0));
  return rows;
}
