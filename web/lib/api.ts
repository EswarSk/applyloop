export const API = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';
export async function request<T>(path: string, method = 'GET'): Promise<T> {
  const response = await fetch(`${API}${path}`, { method });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}
