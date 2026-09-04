export function requestContractDeletionConfirmation(contractNumber: string): string | null {
  return window.prompt(
    `Удалить договор «${contractNumber}»? Исходные документы не удаляются. Проверьте номер и нажмите OK:`,
    contractNumber,
  );
}
