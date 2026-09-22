type ContractOption = {
  id: number;
  number: string;
  title: string;
  contract_kind?: string;
};

type Props = {
  contracts: ContractOption[];
  selectedContractId: number;
  onSelectContract: (contractId: number) => void;
};

export function GprContractContext({ contracts, selectedContractId, onSelectContract }: Props) {
  const financialContracts = contracts.filter((item) => item.contract_kind !== "prime_reference");
  return <section className="card finance-contract-chain gpr-contract-context">
    <div>
      <span className="eyebrow">ОТДЕЛЬНЫЙ РАЗДЕЛ ГПР</span>
      <h2>График работ по договору</h2>
      <p>Здесь находятся только календарный план, зависимости и диаграмма Ганта. Бюджет и плановый ДДС загружаются в разделе «Исполнение и финансы».</p>
    </div>
    <select aria-label="Договор для графика работ" value={selectedContractId} onChange={(event) => onSelectContract(Number(event.target.value))}>
      <option value={0}>Выберите договор для ГПР</option>
      {financialContracts.map((item) => <option value={item.id} key={item.id}>{item.number} — {item.title}</option>)}
    </select>
  </section>;
}
