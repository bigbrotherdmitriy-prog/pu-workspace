export class ApiError extends Error {
  constructor(
    message: string,
    public status: number | null,
    public requestId: string,
    public code?: string,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
