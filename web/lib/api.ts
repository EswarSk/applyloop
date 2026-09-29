export const API = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';
export async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    method,
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = Array.isArray(payload.detail) ? payload.detail.map((d: { msg: string }) => d.msg).join('; ') : payload.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}
