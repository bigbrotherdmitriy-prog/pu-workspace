import { useEffect, useState } from "react";
import { api } from "../../api/client";

type OrganizationProfile = {
  id: number; name: string; legal_name?: string; inn?: string; kpp?: string; ogrn?: string;
  okpo?: string; okato?: string; oktmo?: string; okogu?: string; okved?: string;
  legal_address?: string; postal_address?: string; phone?: string; email?: string;
  director_name?: string; chief_accountant?: string; registration_details?: string; tax_office?: string;
  bank_name?: string; bank_address?: string; settlement_account?: string;
  correspondent_account?: string; bik?: string; requisites_status?: string;
};

const fields: Array<[keyof OrganizationProfile, string]> = [
  ["name", "Краткое наименование"], ["legal_name", "Полное юридическое наименование"],
  ["inn", "ИНН"], ["kpp", "КПП"], ["ogrn", "ОГРН"], ["okpo", "ОКПО"],
  ["okato", "ОКАТО"], ["oktmo", "ОКТМО"], ["okogu", "ОКОГУ"], ["okved", "ОКВЭД"],
  ["legal_address", "Юридический адрес"], ["postal_address", "Почтовый адрес"],
  ["phone", "Телефон"], ["email", "Электронная почта"], ["director_name", "Руководитель"],
  ["chief_accountant", "Главный бухгалтер"], ["registration_details", "Регистрационные сведения"],
  ["tax_office", "Налоговая инспекция"], ["bank_name", "Банк"], ["bank_address", "Адрес банка"],
  ["settlement_account", "Расчётный счёт"], ["correspondent_account", "Корреспондентский счёт"], ["bik", "БИК"],
];

export function CompanyProfileCard({ editable }: { editable: boolean }) {
  const [profile, setProfile] = useState<OrganizationProfile | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!editable) return;
    api<OrganizationProfile>("/organizations/current/requisites")
      .then(setProfile)
      .catch((error) => setMessage(error.message));
  }, [editable]);
  async function save() {
    if (!profile) return;
    try {
      const saved = await api<OrganizationProfile>(`/organizations/${profile.id}`, {
        method: "PUT",
        body: JSON.stringify({ ...profile, requisites_status: "confirmed" }),
      });
      setProfile(saved); setMessage("Реквизиты организации сохранены и подтверждены");
    } catch (error) { setMessage((error as Error).message); }
  }
  if (!editable) return null;
  return <section className="card span-settings company-profile-card">
    <div className="card-head"><div><span className="eyebrow">НАША ОРГАНИЗАЦИЯ</span><h2>{profile?.name || "Карточка предприятия"}</h2><p>Доступно только администратору. Данные используются в договорах, письмах, счетах и документах; реквизиты контрагентов запоминаются отдельно при анализе договоров.</p></div><span className={`draft-status ${profile?.requisites_status === "confirmed" ? "completed" : "proposed"}`}>{profile?.requisites_status === "confirmed" ? "Подтверждено" : "Требует проверки"}</span></div>
    {profile && <div className="form-grid company-profile-grid">{fields.map(([field, label]) => <label key={field}>{label}<input value={String(profile[field] || "")} onChange={(event) => setProfile({ ...profile, [field]: event.target.value })} /></label>)}</div>}
    {!profile && !message && <p>Загрузка карточки предприятия…</p>}
    {message && <p className="finance-warning">{message}</p>}
    {profile && <button onClick={() => void save()}>Сохранить и подтвердить реквизиты</button>}
  </section>;
}
