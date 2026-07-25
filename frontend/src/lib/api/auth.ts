// Cliente de la API de auth. Tipos espejan los schemas del backend
// (backend/routers/auth.py → UserOut, RegisterBody, LoginBody).
import { apiFetch } from './client';

export interface User {
  id: number;
  email: string;
  profile: string;
}

export function register(email: string, password: string, profile: string): Promise<User> {
  return apiFetch<User>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password, profile })
  });
}

export function login(email: string, password: string): Promise<User> {
  return apiFetch<User>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password })
  });
}

export function me(): Promise<User> {
  return apiFetch<User>('/api/auth/me');
}

export function logout(): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>('/api/auth/logout', { method: 'POST' });
}
